"""Supplier adapters and the optional server-side Bolna voice boundary."""

import os

import httpx


class SupplierUnavailable(RuntimeError):
    pass


class MockSupplierAdapter:
    mode = "simulated"

    def get_quotes(self, connection, part_name: str, quantity: int) -> list[dict]:
        rows = connection.execute(
            "SELECT s.id supplier_id, s.name supplier_name, p.name part_name, "
            "q.available, q.unit_price, q.delivery_days, q.response_status "
            "FROM supplier_quotes q JOIN suppliers s ON s.id=q.supplier_id "
            "JOIN parts p ON p.id=q.part_id WHERE lower(p.name)=lower(?)",
            (part_name,),
        ).fetchall()
        return [{**dict(row), "available": bool(row["available"]), "quantity": quantity,
                 "total_price": row["unit_price"] * quantity if row["unit_price"] is not None else None,
                 "is_simulated": True} for row in rows]

    def place_order(self, approved_quote: dict, idempotency_key: str) -> dict:
        # The deterministic demo adapter has no network side effects. The persisted
        # unique approval_id/idempotency_key protects retry and concurrent requests.
        return {"supplier_reference": f"SIM-{idempotency_key}",
                "status": "simulated_confirmed", "is_simulated": True}


class BolnaAdapter:
    base_url = "https://api.bolna.ai"

    def __init__(self, api_key: str | None = None, agent_id: str | None = None, client=None):
        self.api_key = api_key or os.getenv("BOLNA_API_KEY", "").strip()
        self.agent_id = agent_id or os.getenv("BOLNA_AGENT_ID", "").strip()
        self.client = client or httpx.Client(base_url=self.base_url, timeout=15)

    def status(self) -> dict:
        configured = bool(self.api_key and self.agent_id)
        return {
            "provider": "bolna",
            "available": configured,
            "configured": configured,
            "message": "Bolna is configured for server-side calls." if configured
            else "Configure BOLNA_API_KEY and BOLNA_AGENT_ID to enable voice. Text fallback remains available.",
        }

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise RuntimeError("BOLNA_API_KEY is not configured")
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def verify_account(self) -> dict:
        response = self.client.get("/user/me", headers=self._headers())
        response.raise_for_status()
        return response.json()

    def place_call(self, recipient_phone_number: str) -> dict:
        if not self.agent_id:
            raise RuntimeError("BOLNA_AGENT_ID is not configured")
        response = self.client.post(
            "/call",
            headers=self._headers(),
            json={"agent_id": self.agent_id, "recipient_phone_number": recipient_phone_number},
        )
        response.raise_for_status()
        return response.json()

    def get_execution(self, execution_id: str) -> dict:
        response = self.client.get(f"/executions/{execution_id}", headers=self._headers())
        response.raise_for_status()
        return response.json()
