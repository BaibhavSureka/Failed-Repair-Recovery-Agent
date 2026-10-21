"""Behavioral checks for the actual HTTP workflow and its approval boundaries."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json

import pytest


def get(client, path):
    response = client.get(f"/api{path}")
    assert response.status_code == 200, response.text
    return response.json()


def post(client, path, payload):
    response = client.post(f"/api{path}", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def report(client, job_id="JOB-1001", message=None):
    return post(client, "/agent/report", {"message": message or job_id})


def approve(client, job_id="JOB-1001"):
    job = report(client, job_id)["job"]
    return post(
        client,
        f"/approvals/{job['approval']['id']}",
        {"decision": "approved", "actor": "Demo operator"},
    )


def schedule(client, job_id="JOB-1001"):
    job = approve(client, job_id)
    return post(
        client,
        f"/jobs/{job_id}/revisit/confirm",
        {
            "selected_slot": job["revisit"]["slots"][0],
            "customer_confirmed": True,
            "technician_confirmed": True,
            "actor": "Demo operator",
        },
    )


def test_seed_is_synthetic_complete_and_reproducible(client):
    jobs = get(client, "/jobs")
    inventory = get(client, "/inventory")
    dashboard = get(client, "/dashboard")

    assert len(jobs) == 10
    assert len({job["customer_name"] for job in jobs}) == 5
    assert len({job["technician_id"] for job in jobs}) == 3
    assert all(job["is_demo"] is True for job in jobs)
    assert 10 <= len(inventory["parts"]) <= 15
    assert len(inventory["suppliers"]) == 3
    assert dashboard["demo_date"] == "2026-10-21"
    assert dashboard["metrics"]["total_jobs"] == 10
    assert sum(row["count"] for row in dashboard["status_counts"]) == 10

    report(client)
    post(client, "/demo/reset", {"confirm": True})
    assert get(client, "/jobs") == jobs
    assert get(client, "/inventory") == inventory
    assert get(client, "/dashboard")["metrics"] == dashboard["metrics"]


def test_phase_one_health_discloses_voice_is_unavailable(client):
    health = get(client, "/health")
    assert health["status"] == "ok"
    assert health["voice"]["available"] is False
    assert health["voice"]["message"]


def test_complete_recovery_updates_dashboard_and_audit(client):
    before = get(client, "/dashboard")["metrics"]
    response = report(
        client,
        message=(
            "JOB-1001: I visited Demo Restaurant to repair its commercial "
            "refrigerator. The compressor relay is faulty and I do not have "
            "the replacement part. This is urgent."
        ),
    )
    assert response["needs_clarification"] is False
    assert response["extracted"]["job_id"] == "JOB-1001"
    job = response["job"]
    assert job["id"] == "JOB-1001"
    assert job["status"] == "AWAITING_APPROVAL"
    assert job["approval"]["status"] == "pending"
    assert job["order"] is None
    assert job["revisit"] is None
    assert job["equipment_history"]
    assert job["plan"]
    tool_names = {call["name"] for call in response["tool_calls"]}
    assert {
        "get_repair_job",
        "get_equipment_history",
        "check_inventory",
        "get_supplier_quotes",
        "create_recovery_plan",
        "request_human_approval",
    } <= tool_names
    assert all("arguments" in call and "result" in call for call in response["tool_calls"])

    job = post(
        client,
        f"/approvals/{job['approval']['id']}",
        {"decision": "approved", "actor": "Demo operator"},
    )
    assert job["status"] == "AWAITING_REVISIT_CONFIRMATION"
    assert job["approval"]["status"] == "approved"
    assert job["order"]
    assert job["order"].get("simulated", job["order"].get("is_simulated")) is True
    assert job["revisit"]["customer_confirmed"] is False
    assert job["revisit"]["technician_confirmed"] is False
    selected_slot = job["revisit"]["slots"][0]
    assert "2026-10-22" in selected_slot

    job = post(
        client,
        "/jobs/JOB-1001/revisit/confirm",
        {
            "selected_slot": selected_slot,
            "customer_confirmed": True,
            "technician_confirmed": False,
            "actor": "Demo customer",
        },
    )
    assert job["status"] == "AWAITING_REVISIT_CONFIRMATION"
    assert job["revisit"]["customer_confirmed"] is True
    assert job["revisit"]["technician_confirmed"] is False
    assert client.post(
        "/api/jobs/JOB-1001/start-repair", json={"actor": "Demo technician"}
    ).status_code == 409

    job = post(
        client,
        "/jobs/JOB-1001/revisit/confirm",
        {
            "selected_slot": selected_slot,
            "customer_confirmed": True,
            "technician_confirmed": True,
            "actor": "Demo technician",
        },
    )
    assert job["status"] == "REVISIT_SCHEDULED"
    job = post(client, "/jobs/JOB-1001/start-repair", {"actor": "Demo technician"})
    assert job["status"] == "REPAIR_IN_PROGRESS"
    assert get(client, "/dashboard")["metrics"]["resolved"] == before["resolved"]

    evidence = "Replaced compressor relay; refrigerator passed the cooling test."
    job = post(
        client,
        "/jobs/JOB-1001/complete",
        {"confirmed": True, "evidence": evidence, "actor": "Demo technician"},
    )
    assert job["status"] == "RESOLVED"
    assert any(evidence in str(item["details"]) for item in job["history"])
    activities = get(client, "/activity?job_id=JOB-1001")
    assert activities
    assert all(item["job_id"] == "JOB-1001" for item in activities)
    assert all(item["created_at"] for item in activities)
    after = get(client, "/dashboard")["metrics"]
    assert after["resolved"] == before["resolved"] + 1
    assert after["recovery_rate"] > before["recovery_rate"]
    assert after["total_jobs"] == before["total_jobs"]


def test_rejection_never_places_order(client):
    job = report(client)["job"]
    approval_id = job["approval"]["id"]
    rejected = post(
        client,
        f"/approvals/{approval_id}",
        {"decision": "rejected", "actor": "Demo operator"},
    )
    assert rejected["status"] == "ESCALATED"
    assert rejected["approval"]["status"] == "rejected"
    assert rejected["order"] is None
    assert rejected["revisit"] is None
    response = client.post(
        f"/api/approvals/{approval_id}",
        json={"decision": "approved", "actor": "Demo operator"},
    )
    assert response.status_code == 409
    assert get(client, "/jobs/JOB-1001")["order"] is None


def test_repeated_report_and_approval_reuse_plan_order_and_revisit(client):
    first = report(client)["job"]
    repeated = report(client)["job"]
    assert repeated["approval"]["id"] == first["approval"]["id"]
    assert repeated["plan"] == first["plan"]
    approval_path = f"/approvals/{first['approval']['id']}"
    approved = post(client, approval_path, {"decision": "approved", "actor": "Operator"})
    repeated_approval = post(
        client, approval_path, {"decision": "approved", "actor": "Operator"}
    )
    repeated_report = report(client)["job"]
    assert repeated_approval["order"]["id"] == approved["order"]["id"]
    assert repeated_report["order"]["id"] == approved["order"]["id"]
    assert repeated_approval["revisit"]["id"] == approved["revisit"]["id"]
    assert repeated_report["revisit"]["id"] == approved["revisit"]["id"]


def test_concurrent_approval_retries_create_one_order_and_booking(client):
    job = report(client)["job"]
    path = f"/api/approvals/{job['approval']['id']}"

    def submit_approval(_):
        return client.post(path, json={"decision": "approved", "actor": "Operator"})

    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(submit_approval, range(4)))
    assert all(response.status_code == 200 for response in responses), [
        response.text for response in responses
    ]
    jobs = [response.json() for response in responses]
    assert len({job["order"]["id"] for job in jobs}) == 1
    assert len({job["revisit"]["id"] for job in jobs}) == 1


def test_repeated_confirmation_reuses_booking_even_after_appointment_time(client, demo_clock):
    job = approve(client)
    payload = {
        "selected_slot": job["revisit"]["slots"][0],
        "customer_confirmed": True,
        "technician_confirmed": True,
        "actor": "Operator",
    }
    first = post(client, "/jobs/JOB-1001/revisit/confirm", payload)
    demo_clock[0] += timedelta(days=3)
    second = post(client, "/jobs/JOB-1001/revisit/confirm", payload)
    assert second["revisit"] == first["revisit"]
    assert second["status"] == "REVISIT_SCHEDULED"


def test_confirmation_from_second_party_preserves_first_party(client):
    job = approve(client)
    payload = {
        "selected_slot": job["revisit"]["slots"][0],
        "customer_confirmed": True,
        "technician_confirmed": False,
        "actor": "Customer operator",
    }
    post(client, "/jobs/JOB-1001/revisit/confirm", payload)
    payload.update(
        customer_confirmed=False, technician_confirmed=True, actor="Technician operator"
    )
    job = post(client, "/jobs/JOB-1001/revisit/confirm", payload)
    assert job["revisit"]["customer_confirmed"] is True
    assert job["revisit"]["technician_confirmed"] is True
    assert job["status"] == "REVISIT_SCHEDULED"


@pytest.mark.parametrize(
    "message",
    [
        "The commercial refrigerator at Demo Restaurant needs a compressor relay.",
        "The repair failed and I need a replacement part.",
    ],
)
def test_ambiguous_or_unidentified_report_never_changes_jobs(client, message):
    before_jobs = get(client, "/jobs")
    before_activity = get(client, "/activity")
    response = report(client, message=message)
    assert response["needs_clarification"] is True
    assert response["job"] is None
    assert get(client, "/jobs") == before_jobs
    assert get(client, "/activity") == before_activity


def test_unknown_part_is_not_replaced_with_invented_diagnosis(client):
    before = get(client, "/jobs/JOB-1001")
    response = report(
        client,
        message="JOB-1001: the flux capacitor is faulty; I need a replacement flux capacitor.",
    )
    assert response["needs_clarification"] is True
    assert response["job"] is None
    assert get(client, "/jobs/JOB-1001") == before


def test_conflicting_job_identifiers_cannot_update_either_job(client):
    before = get(client, "/jobs")
    response = client.post(
        "/api/agent/report",
        json={"job_id": "JOB-1002", "message": "JOB-1001 requires a compressor relay."},
    )
    assert response.status_code in (200, 409, 422), response.text
    if response.status_code == 200:
        assert response.json()["needs_clarification"] is True
        assert response.json()["job"] is None
    assert get(client, "/jobs") == before


def test_inventory_recovery_avoids_purchase_and_supplier_calls(client):
    response = report(client, "JOB-1002")
    job = response["job"]
    assert job["status"] == "AWAITING_REVISIT_CONFIRMATION"
    assert job["approval"] is None
    assert job["order"] is None
    assert job["revisit"]["slots"]
    assert job["revisit"]["customer_confirmed"] is False
    assert job["revisit"]["technician_confirmed"] is False
    assert "get_supplier_quotes" not in {call["name"] for call in response["tool_calls"]}


def test_no_supplier_availability_escalates_without_order(client):
    response = report(client, "JOB-1003")
    job = response["job"]
    assert job["status"] == "ESCALATED"
    assert job["order"] is None
    assert job["approval"] is None
    assert job["revisit"] is None
    assert "get_supplier_quotes" in {call["name"] for call in response["tool_calls"]}


@pytest.mark.parametrize("phase", ["approval", "confirmation"])
def test_overdue_actions_escalate_once_without_claiming_communications(client, demo_clock, phase):
    if phase == "approval":
        report(client)
    else:
        approve(client)
    before = get(client, "/jobs/JOB-1001")
    assert "JOB-1001" not in post(client, "/followups/run", {})["escalated"]
    demo_clock[0] += timedelta(days=2)
    result = post(client, "/followups/run", {})
    assert "JOB-1001" in result["escalated"]
    assert result["checked"] >= 1
    job = get(client, "/jobs/JOB-1001")
    assert job["status"] == "ESCALATED"
    assert job["order"] == before["order"]
    assert job["approval"]["status"] == before["approval"]["status"]
    assert "JOB-1001" not in post(client, "/followups/run", {})["escalated"]
    assert all(
        event["event_type"] not in {"call_sent", "message_sent", "customer_contacted"}
        for event in job["history"]
    )


def test_human_takeover_records_reason_and_stops_approval(client):
    pending = report(client)["job"]
    reason = "Demo operator will investigate the equipment diagnosis."
    job = post(
        client,
        "/jobs/JOB-1001/escalate",
        {"reason": reason, "actor": "Demo supervisor"},
    )
    assert job["status"] == "ESCALATED"
    assert any(reason in str(event["details"]) for event in job["history"])
    response = client.post(
        f"/api/approvals/{pending['approval']['id']}",
        json={"decision": "approved", "actor": "Demo operator"},
    )
    assert response.status_code == 409
    assert get(client, "/jobs/JOB-1001")["order"] is None


@pytest.mark.parametrize(
    "path,payload",
    [
        ("/jobs/JOB-1001/start-repair", {"actor": "Technician"}),
        (
            "/jobs/JOB-1001/complete",
            {"confirmed": True, "evidence": "Part replaced and tested.", "actor": "Technician"},
        ),
        (
            "/jobs/JOB-1001/revisit/confirm",
            {
                "selected_slot": "2026-10-22T10:00:00Z",
                "customer_confirmed": True,
                "technician_confirmed": True,
                "actor": "Operator",
            },
        ),
    ],
)
def test_invalid_transitions_do_not_change_job(client, path, payload):
    before = get(client, "/jobs/JOB-1001")
    response = client.post(f"/api{path}", json=payload)
    assert response.status_code in (409, 422), response.text
    assert get(client, "/jobs/JOB-1001") == before


def test_non_proposed_revisit_slot_is_rejected(client):
    before = approve(client)
    response = client.post(
        "/api/jobs/JOB-1001/revisit/confirm",
        json={
            "selected_slot": "2099-01-01T10:00:00Z",
            "customer_confirmed": True,
            "technician_confirmed": True,
            "actor": "Operator",
        },
    )
    assert response.status_code in (409, 422), response.text
    assert get(client, "/jobs/JOB-1001") == before


@pytest.mark.parametrize(
    "payload",
    [
        {"confirmed": False, "evidence": "The technician has not verified the repair.", "actor": "Operator"},
        {"confirmed": True, "evidence": "", "actor": "Operator"},
        {"confirmed": True, "evidence": "   ", "actor": "Operator"},
    ],
)
def test_completion_requires_affirmative_confirmation_and_evidence(client, payload):
    schedule(client)
    before = post(client, "/jobs/JOB-1001/start-repair", {"actor": "Technician"})
    response = client.post("/api/jobs/JOB-1001/complete", json=payload)
    assert response.status_code in (409, 422), response.text
    assert get(client, "/jobs/JOB-1001") == before


@pytest.mark.parametrize(
    "path,payload",
    [
        ("/agent/report", {}),
        ("/agent/report", {"message": ""}),
        ("/agent/report", {"message": "   "}),
        ("/agent/report", {"message": "JOB-1001", "unknown_field": "ignored?"}),
        ("/demo/reset", {"confirm": False}),
        ("/jobs/JOB-1001/escalate", {"actor": "Operator", "reason": ""}),
        ("/jobs/JOB-1001/start-repair", {"actor": ""}),
    ],
)
def test_invalid_payloads_are_rejected_without_side_effects(client, path, payload):
    before = get(client, "/jobs")
    response = client.post(f"/api{path}", json=payload)
    assert response.status_code == 422, response.text
    assert get(client, "/jobs") == before


def test_unknown_job_is_404_and_never_mutates_existing_jobs(client):
    before = get(client, "/jobs")
    assert client.get("/api/jobs/JOB-9999").status_code == 404
    response = report(client, message="JOB-9999 needs a compressor relay.")
    assert response["needs_clarification"] is True
    assert response["job"] is None
    assert get(client, "/jobs") == before


def test_repeated_inventory_report_reserves_stock_only_once(client):
    def stock():
        return next(
            part["stock"]
            for part in get(client, "/inventory")["parts"]
            if part["name"] == "Oven thermostat"
        )

    before = stock()
    first = report(client, "JOB-1002")["job"]
    assert stock() == before - 1
    repeated = report(client, "JOB-1002")["job"]
    assert stock() == before - 1
    assert repeated["revisit"]["id"] == first["revisit"]["id"]
    assert repeated["order"] is None


def test_insufficient_inventory_does_not_over_reserve(client, application):
    with application.state.db.transaction() as connection:
        connection.execute("UPDATE parts SET stock=0 WHERE name='Oven thermostat'")
    job = report(client, "JOB-1002")["job"]
    assert job["status"] == "AWAITING_APPROVAL"
    assert job["order"] is None
    assert job["approval"]["status"] == "pending"
    assert next(
        part["stock"]
        for part in get(client, "/inventory")["parts"]
        if part["name"] == "Oven thermostat"
    ) == 0
    with application.state.db.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM reservations WHERE job_id='JOB-1002'"
        ).fetchone()[0] == 0


@pytest.mark.parametrize("other_job_id", ["JOB-1005", "JOB-1004"])
def test_revisit_cannot_double_book_technician_or_customer(client, other_job_id):
    first = approve(client)
    second = report(client, other_job_id)["job"]
    if second["approval"]:
        second = post(
            client,
            f"/approvals/{second['approval']['id']}",
            {"decision": "approved", "actor": "Operator"},
        )
    shared_slots = set(first["revisit"]["slots"]) & set(second["revisit"]["slots"])
    assert shared_slots, "Fixture should propose the same free slot before either booking is confirmed"
    selected_slot = sorted(shared_slots)[0]
    confirmation = {
        "selected_slot": selected_slot,
        "customer_confirmed": True,
        "technician_confirmed": True,
        "actor": "Operator",
    }
    post(client, "/jobs/JOB-1001/revisit/confirm", confirmation)
    before = get(client, f"/jobs/{other_job_id}")
    response = client.post(
        f"/api/jobs/{other_job_id}/revisit/confirm", json=confirmation
    )
    assert response.status_code == 409, response.text
    assert get(client, f"/jobs/{other_job_id}") == before


def test_no_compatible_availability_escalates_without_booking(client, application):
    with application.state.db.transaction() as connection:
        connection.execute(
            "UPDATE customers SET availability=? WHERE id='C-002'",
            (json.dumps(["2026-10-22T10:00:00+05:30"]),),
        )
        connection.execute(
            "UPDATE technicians SET availability=? WHERE id='T-002'",
            (json.dumps(["2026-10-22T14:00:00+05:30"]),),
        )
    job = report(client, "JOB-1002")["job"]
    assert job["status"] == "ESCALATED"
    assert job["revisit"] is None
    assert job["approval"] is None
    assert job["order"] is None


def test_supplier_timeout_retries_safely_then_escalates(client, monkeypatch):
    from app.adapters import MockSupplierAdapter, SupplierUnavailable

    attempts = []

    def unavailable(self, connection, part_name, quantity):
        attempts.append((part_name, quantity))
        raise SupplierUnavailable("Simulated supplier timeout")

    monkeypatch.setattr(MockSupplierAdapter, "get_quotes", unavailable)
    response = report(client)
    assert 2 <= len(attempts) <= 3
    assert len(set(attempts)) == 1
    job = response["job"]
    assert job["status"] == "ESCALATED"
    assert job["approval"] is None
    assert job["order"] is None
    assert job["revisit"] is None


def test_approval_keeps_presented_quote_when_live_supplier_prices_change(client, application):
    pending = report(client)["job"]
    frozen_details = pending["approval"]["details"]
    with application.state.db.transaction() as connection:
        connection.execute(
            "UPDATE supplier_quotes SET unit_price=9999 WHERE supplier_id='SUP-A' AND part_id='P-001'"
        )
    approved = post(
        client,
        f"/approvals/{pending['approval']['id']}",
        {"decision": "approved", "actor": "Operator"},
    )
    assert approved["approval"]["details"] == frozen_details
    assert approved["order"]
    assert approved["order"]["unit_price"] == 1200
    assert approved["order"]["total_price"] == 1200
    assert approved["order"]["quote"] == frozen_details["quote"]


@pytest.mark.parametrize("quantity", ["-2", "1.5", "1/2", "0", "101", "zero"])
def test_invalid_report_quantities_require_clarification_without_mutation(client, quantity):
    before = get(client, "/jobs/JOB-1001")
    result = report(client, message=f"JOB-1001: I need {quantity} compressor relays.")
    assert result["needs_clarification"] is True
    assert "quantity" in result["extracted"]["missing_fields"]
    assert result["job"] is None
    assert get(client, "/jobs/JOB-1001") == before


def test_word_quantity_reserves_requested_inventory_count(client):
    result = report(client, message="JOB-1002: I need two oven thermostats.")
    assert result["needs_clarification"] is False
    assert result["extracted"]["quantity"] == 2
    assert result["job"]["plan"]["quantity"] == 2
    assert result["job"]["approval"] is None
    assert result["job"]["order"] is None
    assert next(
        part["stock"]
        for part in get(client, "/inventory")["parts"]
        if part["name"] == "Oven thermostat"
    ) == 2


def test_quantity_exceeding_stock_sources_full_quantity_without_stock_decrement(client, application):
    result = report(client, message="JOB-1002: I need 5 oven thermostats.")
    assert result["needs_clarification"] is False
    assert result["extracted"]["quantity"] == 5
    job = result["job"]
    assert job["status"] == "AWAITING_APPROVAL"
    quote = job["approval"]["details"]["quote"]
    assert quote["quantity"] == 5
    assert quote["total_price"] == quote["unit_price"] * 5
    assert job["order"] is None
    assert next(
        part["stock"]
        for part in get(client, "/inventory")["parts"]
        if part["name"] == "Oven thermostat"
    ) == 4
    with application.state.db.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM reservations WHERE job_id='JOB-1002'"
        ).fetchone()[0] == 0


def test_report_naming_multiple_customers_cannot_silently_select_the_first(client):
    before_jobs = get(client, "/jobs")
    before_activity = get(client, "/activity")
    result = report(
        client,
        message="The repair may be at Demo Bakery or Demo Office. The oven thermostat is faulty.",
    )
    assert result["needs_clarification"] is True
    assert result["job"] is None
    assert get(client, "/jobs") == before_jobs
    assert get(client, "/activity") == before_activity


def test_past_proposed_slot_cannot_become_a_new_confirmed_booking(client, demo_clock):
    job = approve(client)
    demo_clock[0] += timedelta(days=3)
    before = get(client, "/jobs/JOB-1001")
    response = client.post(
        "/api/jobs/JOB-1001/revisit/confirm",
        json={
            "selected_slot": job["revisit"]["slots"][0],
            "customer_confirmed": True,
            "technician_confirmed": True,
            "actor": "Demo operator",
        },
    )
    assert response.status_code in (409, 422), response.text
    assert get(client, "/jobs/JOB-1001") == before


def test_delayed_approval_cannot_create_order_with_delivery_estimate_in_past(client, demo_clock):
    pending = report(client)["job"]
    demo_clock[0] += timedelta(days=3)
    response = client.post(
        f"/api/approvals/{pending['approval']['id']}",
        json={"decision": "approved", "actor": "Demo operator"},
    )
    assert response.status_code in (200, 409), response.text
    if response.status_code == 409:
        assert get(client, "/jobs/JOB-1001")["order"] is None
        return
    job = response.json()
    assert job["order"]["unit_price"] == pending["approval"]["details"]["quote"]["unit_price"]
    assert job["order"]["expected_delivery"] > demo_clock[0].date().isoformat()
    assert job["revisit"]["slots"][0][:10] >= job["order"]["expected_delivery"]


@pytest.mark.parametrize("job_id", ["JOB-99999", "JOB-ABC"])
def test_malformed_explicit_job_id_does_not_fall_back_to_another_customer_job(client, job_id):
    before = get(client, "/jobs")
    result = report(client, message=f"{job_id}: Demo Bakery needs an oven thermostat.")
    assert result["needs_clarification"] is True
    assert result["job"] is None
    assert get(client, "/jobs") == before


def test_approval_after_midnight_preserves_price_and_updates_delivery_estimate(client, demo_clock):
    pending = report(client)["job"]
    original_details = pending["approval"]["details"]
    demo_clock[0] += timedelta(hours=20)
    approved = post(
        client,
        f"/approvals/{pending['approval']['id']}",
        {"decision": "approved", "actor": "Demo operator"},
    )
    assert approved["approval"]["details"] == original_details
    assert approved["order"]["unit_price"] == original_details["quote"]["unit_price"]
    assert approved["order"]["expected_delivery"] == "2026-10-23"
    assert approved["order"]["quoted_delivery_estimate"] == "2026-10-22"
    assert approved["order"]["delivery_estimate_updated"] is True
    assert approved["revisit"]["slots"][0][:10] == "2026-10-23"
