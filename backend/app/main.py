"""Local, synthetic-data API. Run with: uvicorn app.main:app --reload."""
import hmac
import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import httpx

from .adapters import BolnaAdapter
from .identifiers import normalize_job_id
from .db import Database
from .models import Actor, ApprovalDecision, Completion, Escalation, Report, Reset, RevisitConfirmation, VoiceCall, VoiceToolReport
from .service import JOB_QUERY, RecoveryService, Workflow, as_job


def create_app(db_path: str | None = None, demo_date: str | None = None, clock=None):
    if os.getenv("DEMO_MODE", "true").lower() not in {"1", "true", "yes"}:
        raise RuntimeError("This build only supports DEMO_MODE=true; production authentication and integrations are not enabled")
    requested_date = demo_date or os.getenv("DEMO_DATE", "2026-10-21")
    date.fromisoformat(requested_date)
    database_url = os.getenv("DATABASE_URL")
    path = db_path or os.getenv("DATABASE_PATH", str(Path(__file__).resolve().parents[1] / "data" / "demo.sqlite3"))
    db = Database(path=path, database_url=None if db_path else database_url)
    db.initialize(requested_date)
    bolna = BolnaAdapter()
    voice_tool_secret = os.getenv("VOICE_TOOL_SECRET", "").strip()

    def require_voice_tool_secret(provided: str | None):
        if not voice_tool_secret:
            raise HTTPException(503, "VOICE_TOOL_SECRET is not configured")
        if not provided or not hmac.compare_digest(provided, voice_tool_secret):
            raise HTTPException(401, "Invalid voice tool credentials")
    with db.connect() as conn:
        business_date = conn.execute("SELECT value FROM metadata WHERE key='demo_date'").fetchone()["value"]
        latest_activity = conn.execute("SELECT MAX(created_at) latest FROM activities").fetchone()["latest"]
    synthetic_clock = clock is None
    clock_state = []
    if clock is None:
        # Synthetic business clock advances naturally while this demo process runs.
        # Resume from persisted activity so timestamps cannot regress after restart.
        base = datetime.fromisoformat(business_date).replace(hour=9, tzinfo=timezone.utc)
        if latest_activity:
            base = max(base, datetime.fromisoformat(latest_activity))
        clock_state.extend([base, time.monotonic()])
        clock = lambda: clock_state[0] + timedelta(seconds=time.monotonic() - clock_state[1])
    service = RecoveryService(db, business_date, clock)
    app = FastAPI(title="Failed Repair Recovery — Synthetic Demo", version="1.0.0")
    app.state.db, app.state.service = db, service
    origins = [origin.strip() for origin in os.getenv("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",") if origin.strip()]
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=False, allow_methods=["GET", "POST"], allow_headers=["Content-Type"])

    @app.get("/api/health")
    def health():
        return {"status": "ok", "mode": "synthetic_demo", "voice": bolna.status()}

    @app.get("/api/voice/status")
    def voice_status():
        return bolna.status()

    @app.post("/api/voice/call")
    def voice_call(payload: VoiceCall):
        try:
            return bolna.place_call(payload.recipient_phone_number)
        except httpx.HTTPStatusError as error:
            raise HTTPException(error.response.status_code, "Bolna rejected the call request") from error
        except httpx.HTTPError as error:
            raise HTTPException(502, "Bolna could not be reached") from error
        except RuntimeError as error:
            raise HTTPException(503, str(error)) from error

    @app.get("/api/voice/executions/{execution_id}")
    def voice_execution(execution_id: str):
        if not execution_id or len(execution_id) > 200:
            raise HTTPException(422, "A valid Bolna execution ID is required")
        try:
            return bolna.get_execution(execution_id)
        except httpx.HTTPStatusError as error:
            raise HTTPException(error.response.status_code, "Bolna rejected the execution request") from error
        except httpx.HTTPError as error:
            raise HTTPException(502, "Bolna could not be reached") from error
        except RuntimeError as error:
            raise HTTPException(503, str(error)) from error

    @app.get("/api/voice/tools/job/{job_id}")
    def voice_tool_job(job_id: str, x_voice_tool_secret: str | None = Header(default=None)):
        """Bolna custom tool: retrieve the job context before collecting a report."""
        require_voice_tool_secret(x_voice_tool_secret)
        try:
            return service.read_job(normalize_job_id(job_id))
        except ValueError as error:
            raise HTTPException(422, "Job ID must contain JOB and four digits, such as JOB-1002") from error

    @app.post("/api/voice/tools/report")
    def voice_tool_report(payload: VoiceToolReport, x_voice_tool_secret: str | None = Header(default=None)):
        """Bolna custom tool: submit a spoken technician report to the workflow."""
        require_voice_tool_secret(x_voice_tool_secret)
        result = service.report(message=payload.message, job_id=payload.job_id)
        result["voice_source"] = payload.actor
        return result

    @app.get("/api/jobs")
    def jobs():
        with db.connect() as conn:
            return [as_job(row) for row in conn.execute(JOB_QUERY + " ORDER BY j.id")]

    @app.get("/api/jobs/{job_id}")
    def job_detail(job_id: str):
        return service.read_job(job_id)

    @app.get("/api/activity")
    def activity(job_id: str | None = None):
        with db.connect() as conn:
            workflow = Workflow(service, conn)
            if job_id:
                workflow.get_repair_job(job_id)
            return workflow.activities(job_id)

    @app.get("/api/dashboard")
    def dashboard():
        with db.connect() as conn:
            counts = {row["status"]: row["count"] for row in conn.execute("SELECT status,COUNT(*) count FROM jobs GROUP BY status")}
            total = sum(counts.values())
            resolved = counts.get("RESOLVED", 0)
            recovery_total = total - counts.get("NEW", 0) - counts.get("CANCELLED", 0)
            return {"metrics": {"total_jobs": total,
                                "awaiting_recovery": sum(counts.get(status, 0) for status in ["FAILED_FIRST_VISIT", "INVESTIGATING", "ESCALATED"]),
                                "awaiting_parts": sum(counts.get(status, 0) for status in ["AWAITING_APPROVAL", "PART_ORDERED", "AWAITING_PART"]),
                                "awaiting_confirmation": sum(counts.get(status, 0) for status in ["AWAITING_APPROVAL", "AWAITING_REVISIT_CONFIRMATION", "AWAITING_CUSTOMER"]),
                                "resolved": resolved, "recovery_rate": round(100 * resolved / recovery_total, 1) if recovery_total else 0},
                    "status_counts": [{"status": status, "count": count} for status, count in sorted(counts.items())],
                    "recent_activity": Workflow(service, conn).activities()[:20], "demo_date": business_date,
                    "business_time": service.now().isoformat(),
                    "metric_note": "Recovery rate = resolved / all jobs that reached a repair attempt (excludes new and cancelled); includes seeded historical demos."}

    @app.get("/api/inventory")
    def inventory():
        with db.connect() as conn:
            parts = [dict(row) for row in conn.execute("SELECT * FROM parts ORDER BY id")]
            suppliers = [dict(row) for row in conn.execute("SELECT * FROM suppliers ORDER BY id")]
            quotes = []
            for part in parts:
                # Listing the seeded supplier catalog does not call a live integration.
                quotes.extend(service.supplier.get_quotes(conn, part["name"], 1))
            return {"parts": parts, "suppliers": suppliers, "quotes": quotes}

    @app.post("/api/agent/report")
    def report(payload: Report):
        return service.report(**payload.model_dump())

    @app.post("/api/approvals/{approval_id}")
    def approval(approval_id: str, payload: ApprovalDecision):
        return service.decide_approval(approval_id, **payload.model_dump())

    @app.post("/api/jobs/{job_id}/revisit/confirm")
    def confirm_revisit(job_id: str, payload: RevisitConfirmation):
        return service.confirm_revisit(job_id, **payload.model_dump())

    @app.post("/api/jobs/{job_id}/start-repair")
    def start_repair(job_id: str, payload: Actor):
        return service.start_repair(job_id, **payload.model_dump())

    @app.post("/api/jobs/{job_id}/complete")
    def complete(job_id: str, payload: Completion):
        return service.complete(job_id, **payload.model_dump())

    @app.post("/api/jobs/{job_id}/escalate")
    def escalate(job_id: str, payload: Escalation):
        return service.escalate(job_id, **payload.model_dump())

    @app.post("/api/followups/run")
    def followups():
        return service.followups()

    @app.post("/api/demo/reset")
    def reset(payload: Reset):
        if payload.confirm is not True:
            raise HTTPException(422, "Explicit reset confirmation required")
        db.reset(business_date)
        if synthetic_clock:
            clock_state[:] = [datetime.fromisoformat(business_date).replace(hour=9, tzinfo=timezone.utc), time.monotonic()]
        return {"message": "Deterministic synthetic demo restored"}

    return app


app = create_app()
