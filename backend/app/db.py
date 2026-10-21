import json
import sqlite3
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS customers (id TEXT PRIMARY KEY, name TEXT NOT NULL, availability TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS technicians (id TEXT PRIMARY KEY, name TEXT NOT NULL, availability TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS equipment (id TEXT PRIMARY KEY, customer_id TEXT NOT NULL REFERENCES customers(id), name TEXT NOT NULL, history TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS parts (id TEXT PRIMARY KEY, name TEXT UNIQUE NOT NULL, stock INTEGER NOT NULL CHECK(stock>=0), unit_cost INTEGER NOT NULL CHECK(unit_cost>=0));
CREATE TABLE IF NOT EXISTS suppliers (id TEXT PRIMARY KEY, name TEXT NOT NULL, response_status TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS supplier_quotes (supplier_id TEXT NOT NULL REFERENCES suppliers(id), part_id TEXT NOT NULL REFERENCES parts(id), available INTEGER NOT NULL, unit_price INTEGER, delivery_days INTEGER, response_status TEXT NOT NULL, PRIMARY KEY(supplier_id,part_id));
CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, equipment_id TEXT NOT NULL REFERENCES equipment(id), technician_id TEXT NOT NULL REFERENCES technicians(id), issue TEXT NOT NULL, required_part TEXT NOT NULL, priority TEXT NOT NULL, status TEXT NOT NULL, next_action TEXT NOT NULL, expected_revisit TEXT, due_at TEXT, is_demo INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS plans (id TEXT PRIMARY KEY, job_id TEXT UNIQUE NOT NULL REFERENCES jobs(id), findings TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS approvals (id TEXT PRIMARY KEY, job_id TEXT UNIQUE NOT NULL REFERENCES jobs(id), status TEXT NOT NULL, action TEXT NOT NULL, details TEXT NOT NULL, actor TEXT, decided_at TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS orders (id TEXT PRIMARY KEY, job_id TEXT UNIQUE NOT NULL REFERENCES jobs(id), approval_id TEXT UNIQUE NOT NULL REFERENCES approvals(id), idempotency_key TEXT UNIQUE NOT NULL, details TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS reservations (id TEXT PRIMARY KEY, job_id TEXT UNIQUE NOT NULL REFERENCES jobs(id), part_id TEXT NOT NULL REFERENCES parts(id), quantity INTEGER NOT NULL CHECK(quantity>0), created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS revisits (id TEXT PRIMARY KEY, job_id TEXT UNIQUE NOT NULL REFERENCES jobs(id), status TEXT NOT NULL, slots TEXT NOT NULL, selected_slot TEXT, customer_confirmed INTEGER NOT NULL DEFAULT 0, technician_confirmed INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS one_technician_slot ON revisits(selected_slot,job_id) WHERE status='scheduled';
CREATE TABLE IF NOT EXISTS activities (id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL REFERENCES jobs(id), event_type TEXT NOT NULL, details TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS activity_job ON activities(job_id,id);
"""


class Database:
    def __init__(self, path: str):
        self.path = str(path)
        self._anchor = None
        if self.path == ":memory:":
            self.path = f"file:recovery-{id(self)}?mode=memory&cache=shared"
            self._anchor = sqlite3.connect(self.path, uri=True)
        else:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=15, uri=self.path.startswith("file:"))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 15000")
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(self):
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def initialize(self, demo_date: str):
        with self.connect() as conn:
            conn.executescript(SCHEMA)
        with self.transaction() as conn:
            if not conn.execute("SELECT 1 FROM metadata WHERE key='seed_version'").fetchone():
                seed_connection(conn, demo_date)

    def reset(self, demo_date: str):
        with self.transaction() as conn:
            for table in ["activities", "revisits", "reservations", "orders", "approvals", "plans", "jobs", "supplier_quotes", "suppliers", "parts", "equipment", "technicians", "customers", "metadata"]:
                conn.execute(f"DELETE FROM {table}")
            conn.execute("DELETE FROM sqlite_sequence WHERE name='activities'")
            seed_connection(conn, demo_date)


def seed_connection(conn, demo_date: str):
    base = date.fromisoformat(demo_date)
    timestamp = f"{demo_date}T08:00:00+00:00"
    slots = [(base + timedelta(days=d)).isoformat() + f"T{hour}:00:00+05:30"
             for d in range(1, 15) for hour in ("10", "14")]
    for i, name in enumerate(["Demo Restaurant", "Demo Bakery", "Demo Clinic", "Demo Laundry", "Demo Office"], 1):
        conn.execute("INSERT INTO customers VALUES (?,?,?)", (f"C-{i:03}", name, json.dumps(slots)))
    for i, name in enumerate(["Aarav Mehta", "Mira Shah", "Kabir Rao"], 1):
        conn.execute("INSERT INTO technicians VALUES (?,?,?)", (f"T-{i:03}", name, json.dumps(slots)))
    part_data = [("Compressor relay", 0, 1100), ("Oven thermostat", 4, 850), ("Fan motor", 0, 2100), ("Drain pump", 3, 1450), ("Door gasket", 8, 600), ("Water inlet valve", 5, 750), ("Heating element", 2, 1800), ("Control board", 0, 3200), ("Drive belt", 6, 450), ("Temperature sensor", 7, 350), ("Air filter", 12, 250), ("Contactor", 3, 950)]
    for i, (name, stock, cost) in enumerate(part_data, 1):
        conn.execute("INSERT INTO parts VALUES (?,?,?,?)", (f"P-{i:03}", name, stock, cost))
    for code, name, response in [("A", "Supplier A", "responded"), ("B", "Supplier B", "responded"), ("C", "Supplier C", "responded")]:
        conn.execute("INSERT INTO suppliers VALUES (?,?,?)", (f"SUP-{code}", name, response))
        for i, (part, _, cost) in enumerate(part_data, 1):
            available = part != "Fan motor" and code != "B"
            price = (1200 if code == "A" else 1450) if part == "Compressor relay" else cost + (100 if code == "A" else 350)
            conn.execute("INSERT INTO supplier_quotes VALUES (?,?,?,?,?,?)", (f"SUP-{code}", f"P-{i:03}", int(available), price if available else None, (1 if code == "A" else 3) if available else None, "responded"))
    fixture_jobs = [
        ("C-001", "Commercial refrigerator", "T-001", "Refrigerator is not cooling", "Compressor relay", "high", "FAILED_FIRST_VISIT"),
        ("C-002", "Commercial oven", "T-002", "Oven temperature fluctuates", "Oven thermostat", "medium", "FAILED_FIRST_VISIT"),
        ("C-003", "Air conditioner", "T-003", "Cooling fan will not start", "Fan motor", "high", "FAILED_FIRST_VISIT"),
        ("C-001", "Commercial refrigerator", "T-002", "Intermittent compressor start on second refrigerator", "Compressor relay", "medium", "FAILED_FIRST_VISIT"),
        ("C-004", "Washing machine", "T-001", "Washer does not drain", "Drain pump", "medium", "FAILED_FIRST_VISIT"),
        ("C-005", "Water dispenser", "T-003", "Water inlet leaks", "Water inlet valve", "low", "NEW"),
        ("C-002", "Dishwasher", "T-002", "Door does not seal", "Door gasket", "low", "RESOLVED"),
        ("C-003", "Water heater", "T-003", "Heating restored", "Heating element", "medium", "RESOLVED"),
        ("C-004", "Tumble dryer", "T-001", "Drum stopped rotating", "Drive belt", "medium", "FAILED_FIRST_VISIT"),
        ("C-005", "Air conditioner", "T-002", "Airflow restored", "Air filter", "low", "RESOLVED"),
    ]
    for i, (customer, equipment, tech, issue, part, priority, status) in enumerate(fixture_jobs, 1):
        eq_id, job_id = f"EQ-{i:03}", f"JOB-{1000+i}"
        history = [{"date": (base-timedelta(days=90)).isoformat(), "action": "Synthetic preventive maintenance", "outcome": "Inspection completed"}]
        conn.execute("INSERT INTO equipment VALUES (?,?,?,?)", (eq_id, customer, equipment, json.dumps(history)))
        next_action = "Recovery complete" if status == "RESOLVED" else "Submit technician report" if status == "FAILED_FIRST_VISIT" else "Await first technician visit"
        conn.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?)", (job_id, eq_id, tech, issue, part, priority, status, next_action, None, None, 1))
        details = {"is_demo": True, "source": "deterministic_seed", "status": status}
        if status == "RESOLVED":
            details.update({"completion_confirmed": True, "actor": "Synthetic demo operator", "evidence": "Historical demo repair passed functional checks; completion explicitly recorded in fixture."})
        conn.execute("INSERT INTO activities(job_id,event_type,details,created_at) VALUES (?,?,?,?)", (job_id, "demo_job_seeded", json.dumps(details), timestamp))
    conn.execute("INSERT INTO metadata VALUES ('seed_version','1')")
    conn.execute("INSERT INTO metadata VALUES ('demo_date',?)", (demo_date,))
