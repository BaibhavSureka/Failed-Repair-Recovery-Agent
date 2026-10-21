"""Explicitly simulated supplier responses and a disabled phase-two voice boundary."""


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


class DisabledBolnaAdapter:
    def status(self) -> dict:
        return {"provider": "bolna", "available": False,
                "message": "Phase two: Bolna is not connected. Use the working text recovery workflow."}
