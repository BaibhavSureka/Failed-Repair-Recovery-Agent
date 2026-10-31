import json
import re
from datetime import date, datetime, timedelta, timezone
from typing import Callable

from fastapi import HTTPException

from .adapters import MockSupplierAdapter, SupplierUnavailable
from .identifiers import normalize_job_id


TRANSITIONS = {
    "NEW": {"FAILED_FIRST_VISIT", "ESCALATED", "CANCELLED"},
    "FAILED_FIRST_VISIT": {"INVESTIGATING", "ESCALATED", "CANCELLED"},
    "INVESTIGATING": {"AWAITING_APPROVAL", "AWAITING_REVISIT_CONFIRMATION", "AWAITING_PART", "ESCALATED"},
    "AWAITING_APPROVAL": {"PART_ORDERED", "ESCALATED", "CANCELLED"},
    "PART_ORDERED": {"AWAITING_REVISIT_CONFIRMATION", "AWAITING_PART", "ESCALATED"},
    "AWAITING_PART": {"AWAITING_REVISIT_CONFIRMATION", "ESCALATED"},
    "AWAITING_CUSTOMER": {"AWAITING_REVISIT_CONFIRMATION", "ESCALATED"},
    "AWAITING_REVISIT_CONFIRMATION": {"REVISIT_SCHEDULED", "ESCALATED", "CANCELLED"},
    "REVISIT_SCHEDULED": {"REPAIR_IN_PROGRESS", "ESCALATED", "CANCELLED"},
    "REPAIR_IN_PROGRESS": {"RESOLVED", "ESCALATED"},
    "ESCALATED": set(), "RESOLVED": set(), "CANCELLED": set(),
}

JOB_QUERY = """SELECT j.*, c.name customer_name, e.name equipment_name,
                      t.name technician_name
               FROM jobs j JOIN equipment e ON e.id=j.equipment_id
               JOIN customers c ON c.id=e.customer_id
               JOIN technicians t ON t.id=j.technician_id"""

PART_ALIASES = {
    "compressor relay": ("compressor relay", "start relay"),
    "oven thermostat": ("oven thermostat", "thermostat"),
    "fan motor": ("fan motor",),
    "drain pump": ("drain pump",),
    "door gasket": ("door gasket",),
    "water inlet valve": ("water inlet valve", "inlet valve"),
    "heating element": ("heating element",),
    "control board": ("control board", "pcb"),
    "drive belt": ("drive belt",),
    "temperature sensor": ("temperature sensor",),
    "air filter": ("air filter",),
    "contactor": ("contactor",),
}


def part_alias_matches(value: str, canonical: str) -> bool:
    value = value.strip().lower()
    canonical = canonical.strip().lower()
    return value == canonical or value in PART_ALIASES.get(canonical, ())


def as_job(row) -> dict:
    result = dict(row)
    result.pop("due_at", None)
    result["is_demo"] = bool(result["is_demo"])
    return result


class Workflow:
    """Validated tools operate within the request's single database transaction."""

    def __init__(self, service, connection):
        self.service = service
        self.conn = connection
        self.tool_calls = []

    def now(self) -> str:
        return self.service.now().isoformat()

    def call(self, function, **arguments):
        result = function(**arguments)
        self.tool_calls.append({"name": function.__name__, "arguments": arguments, "result": result})
        return result

    def record_activity(self, job_id: str, event_type: str, details: dict):
        if not isinstance(details, dict) or not event_type:
            raise HTTPException(422, "Activity type and structured details are required")
        self.get_repair_job(job_id)
        cur = self.conn.execute("INSERT INTO activities(job_id,event_type,details,created_at) VALUES (?,?,?,?) RETURNING id",
                                (job_id, event_type, json.dumps(details), self.now()))
        return {"id": cur.fetchone()["id"], "event_type": event_type}

    def get_repair_job(self, job_id: str):
        try:
            job_id = normalize_job_id(job_id)
        except ValueError as error:
            raise HTTPException(422, "Job ID must contain JOB and four digits, such as JOB-1002") from error
        row = self.conn.execute(JOB_QUERY + " WHERE j.id=?", (job_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Repair job not found")
        return as_job(row)

    def search_repair_jobs(self, customer_name: str | None = None, equipment_type: str | None = None):
        if not customer_name and not equipment_type:
            return []
        rows = self.conn.execute(JOB_QUERY + " ORDER BY j.id").fetchall()
        return [as_job(row) for row in rows
                if (not customer_name or customer_name.lower() in row["customer_name"].lower())
                and (not equipment_type or equipment_type.lower() in row["equipment_name"].lower())]

    def get_equipment_history(self, equipment_id: str):
        row = self.conn.execute("SELECT history FROM equipment WHERE id=?", (equipment_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Equipment not found")
        return json.loads(row["history"])

    def check_inventory(self, part_name: str, quantity: int = 1):
        self.validate_quantity(quantity)
        if not isinstance(part_name, str) or not part_name.strip():
            raise HTTPException(422, "A spare part name is required")
        row = self.conn.execute("SELECT * FROM parts WHERE lower(name)=lower(?)", (part_name,)).fetchone()
        if not row:
            rows = self.conn.execute("SELECT * FROM parts").fetchall()
            row = next((candidate for candidate in rows if part_alias_matches(part_name, candidate["name"])), None)
        if not row:
            raise HTTPException(422, "Unknown spare part; clarify the part name")
        return {**dict(row), "quantity": quantity, "available": row["stock"] >= quantity}

    @staticmethod
    def validate_quantity(quantity):
        if not isinstance(quantity, int) or isinstance(quantity, bool) or not 1 <= quantity <= 100:
            raise HTTPException(422, "Quantity must be an integer between 1 and 100")

    def get_supplier_quotes(self, part_name: str, quantity: int = 1):
        self.check_inventory(part_name, quantity)
        return self.service.supplier.get_quotes(self.conn, part_name, quantity)

    def create_recovery_plan(self, job_id: str, findings: dict):
        self.get_repair_job(job_id)
        if not isinstance(findings, dict) or not findings.get("part_name"):
            raise HTTPException(422, "Recovery findings require a part name")
        existing = self.conn.execute("SELECT * FROM plans WHERE job_id=?", (job_id,)).fetchone()
        if existing:
            return {"id": existing["id"], **json.loads(existing["findings"])}
        plan_id = f"PLAN-{job_id}"
        self.conn.execute("INSERT INTO plans VALUES (?,?,?,?)", (plan_id, job_id, json.dumps(findings), self.now()))
        self.record_activity(job_id, "recovery_plan_created", findings)
        return {"id": plan_id, **findings}

    def request_human_approval(self, action: str, details: dict):
        job_id = details.get("job_id")
        if action != "simulated_part_order" or not job_id or not details.get("quote"):
            raise HTTPException(422, "Only a quoted simulated part order can request approval")
        job = self.get_repair_job(job_id)
        if job["status"] != "INVESTIGATING":
            raise HTTPException(409, "Approval requires an investigating job")
        plan = self.conn.execute("SELECT findings FROM plans WHERE job_id=?", (job_id,)).fetchone()
        if not plan or details["quote"] != json.loads(plan["findings"]).get("quote"):
            raise HTTPException(422, "Approval must refer to the saved recovery plan's exact quote")
        approval_id = f"APR-{job_id}"
        self.conn.execute("INSERT INTO approvals VALUES (?,?,?,?,?,?,?,?)",
                          (approval_id, job_id, "pending", action, json.dumps(details), None, None, self.now()))
        self.update_job_status(job_id, "AWAITING_APPROVAL")
        self.set_pending(job_id, "Approve the simulated part order")
        self.record_activity(job_id, "approval_requested", {"approval_id": approval_id, **details})
        return {"id": approval_id, "status": "pending", "details": details}

    def record_approval(self, approval_id: str, decision: str, actor: str = "Demo operator"):
        if decision not in {"approved", "rejected"} or not actor.strip():
            raise HTTPException(422, "Explicit approval decision and actor are required")
        row = self.conn.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Approval not found")
        if row["status"] != "pending":
            if row["status"] != decision:
                raise HTTPException(409, "An approval decision cannot be reversed")
            return dict(row)
        job = self.get_repair_job(row["job_id"])
        if job["status"] != "AWAITING_APPROVAL":
            raise HTTPException(409, "This approval is no longer actionable; a human must review the job")
        due = self.conn.execute("SELECT due_at FROM jobs WHERE id=?", (row["job_id"],)).fetchone()["due_at"]
        if decision == "approved" and due and datetime.fromisoformat(due) <= self.service.now():
            raise HTTPException(409, "This approval has expired. Run pending follow-ups and request human review before expenditure")
        self.conn.execute("UPDATE approvals SET status=?,actor=?,decided_at=? WHERE id=?", (decision, actor, self.now(), approval_id))
        self.record_activity(row["job_id"], "approval_recorded", {"approval_id": approval_id, "decision": decision, "actor": actor})
        return {**dict(row), "status": decision, "actor": actor}

    def update_job_status(self, job_id: str, status: str):
        job = self.get_repair_job(job_id)
        before = job["status"]
        if status not in TRANSITIONS:
            raise HTTPException(422, "Unknown repair status")
        if before == status:
            return {"previous": before, "current": status, "changed": False}
        if status not in TRANSITIONS[before]:
            raise HTTPException(409, f"Invalid transition: {before} → {status}")
        # Enforce the data prerequisites even for internal tool callers.
        if status == "PART_ORDERED" and not self.conn.execute("SELECT 1 FROM orders WHERE job_id=?", (job_id,)).fetchone():
            raise HTTPException(409, "An approved order is required")
        if status == "AWAITING_APPROVAL" and not self.conn.execute("SELECT 1 FROM approvals WHERE job_id=? AND status='pending'", (job_id,)).fetchone():
            raise HTTPException(409, "A pending approval request is required")
        if status == "AWAITING_REVISIT_CONFIRMATION" and not self.conn.execute("SELECT 1 FROM revisits WHERE job_id=? AND status='pending'", (job_id,)).fetchone():
            raise HTTPException(409, "A persisted revisit proposal is required")
        if status == "REPAIR_IN_PROGRESS" and not self.conn.execute("SELECT 1 FROM revisits WHERE job_id=? AND status='scheduled'", (job_id,)).fetchone():
            raise HTTPException(409, "A scheduled revisit is required")
        if status == "REVISIT_SCHEDULED":
            booking = self.conn.execute("SELECT * FROM revisits WHERE job_id=?", (job_id,)).fetchone()
            if not booking or not booking["customer_confirmed"] or not booking["technician_confirmed"]:
                raise HTTPException(409, "Customer and technician confirmations are required")
        if status == "RESOLVED" and not self.conn.execute("SELECT 1 FROM activities WHERE job_id=? AND event_type='completion_confirmed'", (job_id,)).fetchone():
            raise HTTPException(409, "Explicit completion evidence is required")
        self.conn.execute("UPDATE jobs SET status=? WHERE id=?", (status, job_id))
        self.record_activity(job_id, "status_changed", {"from": before, "to": status})
        return {"previous": before, "current": status, "changed": True}

    def set_pending(self, job_id: str, next_action: str, hours: int = 24):
        due = (self.service.now() + timedelta(hours=hours)).isoformat()
        self.conn.execute("UPDATE jobs SET next_action=?,due_at=? WHERE id=?", (next_action, due, job_id))

    def escalate_recovery(self, job_id: str, reason: str, actor: str = "Recovery workflow"):
        if len(reason.strip()) < 5:
            raise HTTPException(422, "An escalation reason is required")
        job = self.get_repair_job(job_id)
        if job["status"] == "ESCALATED":
            return {"status": "ESCALATED", "reason": reason}
        self.update_job_status(job_id, "ESCALATED")
        self.conn.execute("UPDATE jobs SET next_action=?,due_at=NULL WHERE id=?", ("Human review: " + reason, job_id))
        # A takeover cancels an unconfirmed proposal so it cannot later consume a slot.
        self.conn.execute("UPDATE revisits SET status='cancelled' WHERE job_id=? AND status='pending'", (job_id,))
        self.record_activity(job_id, "recovery_escalated", {"reason": reason, "actor": actor, "external_notification_sent": False})
        return {"status": "ESCALATED", "reason": reason}

    def available_slots(self, job_id: str, delivery_days: int = 0):
        job = self.get_repair_job(job_id)
        row = self.conn.execute("SELECT c.availability customer_slots,t.availability technician_slots FROM jobs j JOIN equipment e ON e.id=j.equipment_id JOIN customers c ON c.id=e.customer_id JOIN technicians t ON t.id=j.technician_id WHERE j.id=?", (job_id,)).fetchone()
        earliest = max(date.fromisoformat(self.service.demo_date), self.service.now().date()) + timedelta(days=max(1, delivery_days))
        common = set(json.loads(row["customer_slots"])) & set(json.loads(row["technician_slots"]))
        occupied = {r["selected_slot"] for r in self.conn.execute("SELECT r.selected_slot FROM revisits r JOIN jobs j ON j.id=r.job_id JOIN equipment e ON e.id=j.equipment_id WHERE (j.technician_id=? OR e.customer_id=(SELECT customer_id FROM equipment WHERE id=?)) AND r.status='scheduled' AND j.id<>?", (job["technician_id"], job["equipment_id"], job_id))}
        return sorted(slot for slot in common if date.fromisoformat(slot[:10]) >= earliest and slot not in occupied)[:4]

    def propose_revisit(self, job_id: str, available_slots: list[str]):
        job = self.get_repair_job(job_id)
        existing = self.conn.execute("SELECT * FROM revisits WHERE job_id=?", (job_id,)).fetchone()
        if existing:
            return self.revisit_dict(existing)
        if job["status"] not in {"INVESTIGATING", "PART_ORDERED", "AWAITING_PART", "AWAITING_CUSTOMER"}:
            raise HTTPException(409, "Job is not ready for a revisit proposal")
        if not self.conn.execute("SELECT 1 FROM reservations WHERE job_id=? UNION ALL SELECT 1 FROM orders WHERE job_id=?", (job_id, job_id)).fetchone():
            raise HTTPException(409, "Reserve the part or create an approved simulated order before proposing a revisit")
        if not isinstance(available_slots, list) or any(not isinstance(slot, str) for slot in available_slots):
            raise HTTPException(422, "Available slots must be a list of appointment timestamps")
        if not available_slots:
            self.escalate_recovery(job_id, "No matching customer and technician availability")
            return {"status": "unavailable", "slots": []}
        self.conn.execute("INSERT INTO revisits VALUES (?,?,?,?,?,?,?,?)", (f"REV-{job_id}", job_id, "pending", json.dumps(available_slots), None, 0, 0, self.now()))
        self.update_job_status(job_id, "AWAITING_REVISIT_CONFIRMATION")
        self.set_pending(job_id, "Record customer and technician confirmation for a proposed revisit")
        self.record_activity(job_id, "revisit_proposed", {"slots": available_slots, "customer_confirmed": False, "technician_confirmed": False, "delivery_is_estimate": True, "messages_sent": False})
        return self.revisit_dict(self.conn.execute("SELECT * FROM revisits WHERE job_id=?", (job_id,)).fetchone())

    @staticmethod
    def revisit_dict(row):
        if not row:
            return None
        return {**dict(row), "slots": json.loads(row["slots"]), "customer_confirmed": bool(row["customer_confirmed"]), "technician_confirmed": bool(row["technician_confirmed"])}

    def confirm_revisit(self, job_id: str, selected_slot: str, customer_confirmed: bool = False, technician_confirmed: bool = False, actor: str = "Demo operator"):
        if type(customer_confirmed) is not bool or type(technician_confirmed) is not bool:
            raise HTTPException(422, "Party confirmations must be explicit booleans")
        if not isinstance(actor, str) or not actor.strip():
            raise HTTPException(422, "An actor is required to record confirmations")
        job = self.get_repair_job(job_id)
        row = self.conn.execute("SELECT * FROM revisits WHERE job_id=?", (job_id,)).fetchone()
        if not row or selected_slot not in json.loads(row["slots"]):
            raise HTTPException(422, "Select one of this job's proposed revisit slots")
        if row["selected_slot"] and row["selected_slot"] != selected_slot:
            raise HTTPException(409, "Confirmations already refer to a different slot; human review is required")
        # Replaying a completed booking is a read, even after its appointment time.
        if row["status"] == "scheduled" and job["status"] in {"REVISIT_SCHEDULED", "REPAIR_IN_PROGRESS", "RESOLVED"}:
            return self.revisit_dict(row)
        try:
            selected_time = datetime.fromisoformat(selected_slot)
        except ValueError:
            raise HTTPException(422, "The proposed slot must be a valid appointment timestamp")
        if selected_time.tzinfo is None:
            raise HTTPException(422, "The proposed appointment must include a time zone")
        if selected_time <= self.service.now():
            raise HTTPException(409, "That proposed revisit window has passed; request human review for a new appointment")
        if job["status"] != "AWAITING_REVISIT_CONFIRMATION":
            raise HTTPException(409, "This job is not awaiting revisit confirmation")
        customer = bool(row["customer_confirmed"] or customer_confirmed)
        technician = bool(row["technician_confirmed"] or technician_confirmed)
        if customer and technician:
            conflict = self.conn.execute("SELECT r.job_id FROM revisits r JOIN jobs j ON j.id=r.job_id JOIN equipment e ON e.id=j.equipment_id WHERE (j.technician_id=? OR e.customer_id=(SELECT customer_id FROM equipment WHERE id=?)) AND r.selected_slot=? AND r.status='scheduled' AND j.id<>?", (job["technician_id"], job["equipment_id"], selected_slot, job_id)).fetchone()
            if conflict:
                raise HTTPException(409, "Technician or customer slot is already booked; request human review")
        self.conn.execute("UPDATE revisits SET selected_slot=?,customer_confirmed=?,technician_confirmed=?,status=? WHERE job_id=?", (selected_slot, int(customer), int(technician), "scheduled" if customer and technician else "pending", job_id))
        self.record_activity(job_id, "revisit_confirmation_recorded", {"selected_slot": selected_slot, "customer_confirmed": customer, "technician_confirmed": technician, "actor": actor, "is_simulated": True})
        if customer and technician:
            self.update_job_status(job_id, "REVISIT_SCHEDULED")
            due = (datetime.fromisoformat(selected_slot) + timedelta(hours=4)).astimezone(timezone.utc).isoformat()
            self.conn.execute("UPDATE jobs SET expected_revisit=?,next_action=?,due_at=? WHERE id=?", (selected_slot, "Record technician arrival and start repair", due, job_id))
        return self.revisit_dict(self.conn.execute("SELECT * FROM revisits WHERE job_id=?", (job_id,)).fetchone())

    def job_detail(self, job_id: str):
        job = self.get_repair_job(job_id)
        job["history"] = self.activities(job_id)
        job["equipment_history"] = self.get_equipment_history(job["equipment_id"])
        for table, key, column in [("plans", "plan", "findings"), ("approvals", "approval", "details"), ("orders", "order", "details")]:
            row = self.conn.execute(f"SELECT * FROM {table} WHERE job_id=?", (job_id,)).fetchone()
            if row:
                result = dict(row)
                content = json.loads(result[column])
                if key == "approval":
                    result[column] = content
                else:
                    result.pop(column)
                    result.update(content)
                job[key] = result
            else:
                job[key] = None
        job["revisit"] = self.revisit_dict(self.conn.execute("SELECT * FROM revisits WHERE job_id=?", (job_id,)).fetchone())
        return job

    def activities(self, job_id: str | None = None):
        query = "SELECT * FROM activities" + (" WHERE job_id=?" if job_id else "") + " ORDER BY id DESC"
        rows = self.conn.execute(query, (job_id,) if job_id else ()).fetchall()
        return [{**dict(row), "details": json.loads(row["details"])} for row in rows]


class RecoveryService:
    def __init__(self, db, demo_date: str, clock: Callable[[], datetime]):
        self.db, self.demo_date, self.clock = db, demo_date, clock
        self.supplier = MockSupplierAdapter()

    def now(self):
        current = self.clock()
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return current.astimezone(timezone.utc)

    def read_job(self, job_id):
        with self.db.connect() as conn:
            return Workflow(self, conn).job_detail(job_id)

    def extract(self, message: str, supplied_id: str | None, w: Workflow):
        lower = message.lower()
        ids = set(re.findall(r"\bJOB[- ]?(\d{4})\b", message, re.I))
        ids = {"JOB-" + value for value in ids}
        explicit_id_tokens = re.findall(r"\bJOB(?:-|(?=\d))([\w-]+)|\bJOB\s+(\d[\w-]*)", message, re.I)
        malformed_id = any(not re.fullmatch(r"\d{4}", left or right) for left, right in explicit_id_tokens)
        if supplied_id:
            ids.add(supplied_id)
        parts = [row["name"] for row in w.conn.execute("SELECT name FROM parts")]
        aliases = {"Compressor relay": ["compressor relay", "start relay"], "Oven thermostat": ["oven thermostat", "thermostat"], "Fan motor": ["fan motor"], "Drain pump": ["drain pump"], "Door gasket": ["door gasket"], "Water inlet valve": ["water inlet valve", "inlet valve"], "Heating element": ["heating element"], "Control board": ["control board", "pcb"], "Drive belt": ["drive belt"], "Temperature sensor": ["temperature sensor"], "Air filter": ["air filter"], "Contactor": ["contactor"]}
        matched = [part for part in parts if any(re.search(r"\b" + re.escape(alias) + r"s?\b", lower) for alias in aliases.get(part, [part.lower()]))]
        customers = [name for name in ["Restaurant", "Bakery", "Clinic", "Laundry", "Office"] if re.search(r"\b" + name.lower() + r"\b", lower)]
        customer = customers[0] if len(customers) == 1 else None
        equipment_matches = [name for name, words in [("Commercial refrigerator", ["refrigerator", "fridge"]), ("Commercial oven", ["oven"]), ("Air conditioner", ["air conditioner", "ac unit"]), ("Washing machine", ["washing machine", "washer"]), ("Water dispenser", ["water dispenser"]), ("Dishwasher", ["dishwasher"]), ("Water heater", ["water heater"]), ("Tumble dryer", ["dryer"])] if any(re.search(r"\b" + re.escape(word) + r"\b", lower) for word in words)]
        equipment = equipment_matches[0] if len(equipment_matches) == 1 else None
        # Preserve the whole numeric token: parsing only its integer suffix would
        # silently turn '-2', '1.5', or '1/2' into an unintended purchase quantity.
        number = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:/\d+)?"
        part_prefix = r"(?:units?|compressor|oven|fan|drain|door|water|heating|control|drive|temperature|air|contactor|relays?|thermostats?|pumps?)\b"
        quantity_match = re.search(
            rf"(?:quantity\s*[:=]?\s*|qty\s*[:=]?\s*|need\s+|requires?\s+)({number})(?![\w./])|(?<![\w./])({number})\s+{part_prefix}",
            lower,
        )
        quantity_token = next((group for group in quantity_match.groups() if group is not None), "1") if quantity_match else "1"
        word_quantities = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "zero": 0}
        word_match = re.search(rf"\b({'|'.join(word_quantities)})\s+{part_prefix}", lower)
        invalid_quantity = not re.fullmatch(r"\+?\d+", quantity_token)
        quantity = int(quantity_token) if not invalid_quantity else None
        if not quantity_match and word_match:
            quantity = word_quantities[word_match.group(1)]
        bare_id = bool(re.fullmatch(r"\s*(?:job[- ]?\d{4})\s*[.!]?\s*", message, re.I))
        extracted = {"job_id": next(iter(ids)) if len(ids) == 1 else None, "customer_name": customer, "equipment": equipment, "issue": message, "missing_part": matched[0] if len(matched) == 1 else None, "quantity": quantity, "urgency": "high" if any(word in lower for word in ["urgent", "high priority", "food", "restaurant", "emergency"]) else "medium", "confidence": 0.95 if len(ids) == 1 and len(matched) == 1 else 0.65, "missing_fields": [], "interpreter": "deterministic_demo"}
        error = None
        if malformed_id:
            error = "A job identifier in the report is malformed. Provide one ID in JOB-1001 format."
            extracted["missing_fields"].append("job_id")
        elif len(ids) > 1:
            error = "The selected job and report contain different job IDs. Specify one job ID."
            extracted["missing_fields"].append("job_id")
        elif len(customers) > 1 or len(equipment_matches) > 1:
            error = "The report mentions multiple customers or equipment types. Clarify one repair and its job ID."
            extracted["missing_fields"].append("job_id")
        elif len(matched) > 1:
            error = "Several spare parts were mentioned. Report one required part at a time."
            extracted["missing_fields"].append("missing_part")
        elif invalid_quantity or quantity is None or not 1 <= quantity <= 100:
            error = "Specify a quantity between 1 and 100."
            extracted["missing_fields"].append("quantity")
        elif not matched and not bare_id:
            error = "Specify a supported spare part, such as compressor relay. An unknown or missing part cannot be ordered."
            extracted["missing_fields"].append("missing_part")
        return extracted, error, bare_id

    def report(self, message: str, job_id: str | None = None):
        with self.db.transaction() as conn:
            w = Workflow(self, conn)
            extracted, error, bare_id = self.extract(message, job_id, w)
            response = {"message": "", "needs_clarification": True, "extracted": extracted, "candidates": [], "job": None, "tool_calls": w.tool_calls}
            if error:
                response["message"] = error
                return response
            if extracted["job_id"]:
                try:
                    job = w.call(w.get_repair_job, job_id=extracted["job_id"])
                except HTTPException as exc:
                    if exc.status_code != 404:
                        raise
                    response["message"] = "That job ID does not exist. Check the job ID before continuing."
                    extracted["missing_fields"].append("job_id")
                    return response
            else:
                candidates = w.call(w.search_repair_jobs, customer_name=extracted["customer_name"], equipment_type=extracted["equipment"])
                if len(candidates) != 1:
                    response.update({"message": "Provide the exact job ID to identify this repair confidently.", "candidates": candidates})
                    extracted["missing_fields"].append("job_id")
                    return response
                job = candidates[0]
                extracted["job_id"] = job["id"]
            if bare_id:
                extracted.update({"missing_part": job["required_part"], "equipment": job["equipment_name"], "issue": job["issue"], "urgency": job["priority"], "confidence": 1.0})
            if not part_alias_matches(extracted["missing_part"], job["required_part"]):
                response["message"] = f"This job records {job['required_part']}. Clarify the different part before changing its recovery plan."
                extracted["missing_fields"].append("missing_part")
                return response
            extracted["missing_part"] = job["required_part"]
            if extracted["equipment"] and extracted["equipment"].lower() != job["equipment_name"].lower():
                response["message"] = "The equipment in the report differs from the selected job. Clarify the job ID."
                extracted["missing_fields"].append("job_id")
                return response
            if extracted["customer_name"] and extracted["customer_name"].lower() not in job["customer_name"].lower():
                response["message"] = "The customer in the report differs from the selected job. Clarify the job ID."
                extracted["missing_fields"].append("job_id")
                return response
            response["needs_clarification"] = False
            if job["status"] not in {"NEW", "FAILED_FIRST_VISIT"}:
                response.update({"message": "Recovery already exists. " + job["next_action"], "job": w.job_detail(job["id"])})
                return response
            jid = job["id"]
            w.call(w.record_activity, job_id=jid, event_type="technician_report_received", details={"message": message, "extracted": extracted, "source": "text", "is_demo": True})
            w.record_activity(jid, "job_retrieved", {"job_id": jid, "customer_name": job["customer_name"], "equipment_id": job["equipment_id"], "equipment_name": job["equipment_name"]})
            if job["status"] == "NEW":
                w.call(w.update_job_status, job_id=jid, status="FAILED_FIRST_VISIT")
            w.call(w.update_job_status, job_id=jid, status="INVESTIGATING")
            history = w.call(w.get_equipment_history, equipment_id=job["equipment_id"])
            w.record_activity(jid, "equipment_history_retrieved", {"equipment_id": job["equipment_id"], "history": history})
            inventory = w.call(w.check_inventory, part_name=extracted["missing_part"], quantity=extracted["quantity"])
            w.record_activity(jid, "inventory_checked", inventory)
            findings = {"problem": job["issue"], "part_name": inventory["name"], "quantity": extracted["quantity"], "equipment_history": history, "currency": "INR", "is_demo": True}
            if inventory["available"]:
                conn.execute("UPDATE parts SET stock=stock-? WHERE id=? AND stock>=?", (extracted["quantity"], inventory["id"], extracted["quantity"]))
                conn.execute("INSERT INTO reservations VALUES (?,?,?,?,?)", (f"RES-{jid}", jid, inventory["id"], extracted["quantity"], w.now()))
                w.record_activity(jid, "inventory_reserved", {"part_name": inventory["name"], "quantity": extracted["quantity"], "new_expenditure": 0})
                findings.update({"source": "inventory", "total_cost": 0, "recommendation": "Reserve existing inventory; no new purchase is required.", "next_action": "Confirm a revisit window"})
                w.call(w.create_recovery_plan, job_id=jid, findings=findings)
                revisit = w.call(w.propose_revisit, job_id=jid, available_slots=w.available_slots(jid))
                response["message"] = ("The part was reserved from demo inventory. Confirm both the customer and technician for a proposed revisit."
                                       if revisit["status"] == "pending" else
                                       "The part is reserved, but no compatible revisit window exists. Human review is required.")
            else:
                quotes = None
                for attempt in range(1, 4):
                    try:
                        quotes = w.call(w.get_supplier_quotes, part_name=inventory["name"], quantity=extracted["quantity"])
                        break
                    except (SupplierUnavailable, TimeoutError, ConnectionError) as exc:
                        failure = {"attempt": attempt, "error": type(exc).__name__, "safe_to_retry": True}
                        w.tool_calls.append({"name": "get_supplier_quotes", "arguments": {"part_name": inventory["name"], "quantity": extracted["quantity"]}, "result": failure})
                        w.record_activity(jid, "supplier_lookup_failed", failure)
                if quotes is None:
                    w.call(w.escalate_recovery, job_id=jid, reason="Supplier lookup failed after three safe retries")
                    response["message"] = "Supplier lookup failed after three attempts. Human review is required; no order was placed."
                else:
                    w.record_activity(jid, "supplier_quotes_compared", {"quotes": quotes, "ranking": "earliest delivery, then lowest total cost"})
                    eligible = [quote for quote in quotes if quote["available"] and quote["response_status"] == "responded" and quote["delivery_days"] is not None and quote["unit_price"] is not None]
                    eligible.sort(key=lambda q: (q["delivery_days"], q["total_price"], q["supplier_id"]))
                    if not eligible:
                        w.call(w.escalate_recovery, job_id=jid, reason="No simulated supplier has confirmed availability for the required part")
                        response["message"] = "No supplier has this part available. Human review is required; no order was placed."
                    else:
                        quote = eligible[0]
                        expected = (max(date.fromisoformat(self.demo_date), self.now().date()) + timedelta(days=quote["delivery_days"])).isoformat()
                        findings.update({"source": "supplier", "quote": quote, "quotes": quotes, "total_cost": quote["total_price"], "expected_delivery": expected, "delivery_is_estimate": True, "recommendation": f"{quote['supplier_name']} offers the earliest available delivery ({quote['delivery_days']} day), with cost used to break delivery ties.", "next_action": "Human approval required before a simulated order"})
                        w.call(w.create_recovery_plan, job_id=jid, findings=findings)
                        w.call(w.request_human_approval, action="simulated_part_order", details={"job_id": jid, "quote": quote, "expected_delivery": expected, "currency": "INR", "is_simulated": True})
                        response["message"] = f"Recommend {quote['supplier_name']} at ₹{quote['total_price']:,}, estimated delivery {expected}. Explicit approval is required before placing the simulated order."
            w.record_activity(jid, "workflow_tools_executed", {"tool_calls": w.tool_calls, "interpreter": "deterministic_demo"})
            response["job"] = w.job_detail(jid)
            return response

    def decide_approval(self, approval_id: str, decision: str, actor: str):
        with self.db.transaction() as conn:
            w = Workflow(self, conn)
            row = w.record_approval(approval_id, decision, actor)
            jid = row["job_id"]
            if decision == "rejected":
                w.escalate_recovery(jid, "The simulated part order was rejected by the operator", actor)
                return w.job_detail(jid)
            if conn.execute("SELECT 1 FROM orders WHERE approval_id=?", (approval_id,)).fetchone():
                return w.job_detail(jid)
            if w.get_repair_job(jid)["status"] != "AWAITING_APPROVAL":
                return w.job_detail(jid)
            details = json.loads(row["details"])
            quote = details["quote"]  # Freeze the approved quote; never read mutable pricing here.
            key = f"order-{approval_id}"
            try:
                result = self.supplier.place_order(quote, key)
            except (SupplierUnavailable, TimeoutError, ConnectionError) as exc:
                w.record_activity(jid, "simulated_order_failed", {"error": type(exc).__name__, "idempotency_key": key})
                w.escalate_recovery(jid, "Approved simulated order failed; human review required")
                return w.job_detail(jid)
            current_delivery_estimate = (self.now().date() + timedelta(days=quote["delivery_days"])).isoformat()
            order = {**details, **quote, **result, "approved_by": actor, "delivery_is_estimate": True,
                     "quoted_delivery_estimate": details["expected_delivery"],
                     "expected_delivery": current_delivery_estimate,
                     "delivery_estimate_updated": current_delivery_estimate != details["expected_delivery"]}
            conn.execute("INSERT INTO orders VALUES (?,?,?,?,?,?)", (f"ORD-{jid}", jid, approval_id, key, json.dumps(order), w.now()))
            w.record_activity(jid, "order_created", {"order_id": f"ORD-{jid}", "approval_id": approval_id, **order})
            w.update_job_status(jid, "PART_ORDERED")
            w.propose_revisit(jid, w.available_slots(jid, quote["delivery_days"]))
            return w.job_detail(jid)

    def confirm_revisit(self, job_id, **fields):
        with self.db.transaction() as conn:
            w = Workflow(self, conn)
            w.confirm_revisit(job_id, **fields)
            return w.job_detail(job_id)

    def start_repair(self, job_id, actor):
        with self.db.transaction() as conn:
            w = Workflow(self, conn)
            job = w.get_repair_job(job_id)
            if job["status"] == "REPAIR_IN_PROGRESS":
                return w.job_detail(job_id)
            if job["status"] != "REVISIT_SCHEDULED":
                raise HTTPException(409, "Start repair only after the revisit is confirmed by both parties")
            w.update_job_status(job_id, "REPAIR_IN_PROGRESS")
            w.set_pending(job_id, "Explicitly confirm completion with repair evidence", hours=8)
            w.record_activity(job_id, "repair_started", {"actor": actor, "is_simulated": True})
            return w.job_detail(job_id)

    def complete(self, job_id, confirmed, evidence, actor):
        with self.db.transaction() as conn:
            w = Workflow(self, conn)
            job = w.get_repair_job(job_id)
            if not confirmed or len(evidence.strip()) < 5:
                raise HTTPException(422, "Explicit affirmative completion confirmation and evidence are required")
            if job["status"] == "RESOLVED":
                return w.job_detail(job_id)
            if job["status"] != "REPAIR_IN_PROGRESS":
                raise HTTPException(409, "Completion requires a confirmed revisit and repair in progress")
            w.record_activity(job_id, "completion_confirmed", {"confirmed": True, "evidence": evidence, "actor": actor, "is_simulated": True})
            w.update_job_status(job_id, "RESOLVED")
            conn.execute("UPDATE jobs SET next_action='Recovery complete — repair verified',due_at=NULL WHERE id=?", (job_id,))
            return w.job_detail(job_id)

    def escalate(self, job_id, reason, actor):
        with self.db.transaction() as conn:
            w = Workflow(self, conn)
            w.escalate_recovery(job_id, reason, actor)
            return w.job_detail(job_id)

    def followups(self):
        with self.db.transaction() as conn:
            w = Workflow(self, conn)
            pending = conn.execute("SELECT id,status,due_at FROM jobs WHERE due_at IS NOT NULL AND status NOT IN ('RESOLVED','ESCALATED','CANCELLED')").fetchall()
            escalated = []
            for row in pending:
                if datetime.fromisoformat(row["due_at"]) <= self.now():
                    reason = f"Overdue action in {row['status']}; no external response has been received"
                    w.record_activity(row["id"], "followup_due", {"due_at": row["due_at"], "external_message_sent": False})
                    w.escalate_recovery(row["id"], reason)
                    escalated.append(row["id"])
            return {"escalated": escalated, "checked": len(pending)}
