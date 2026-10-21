# Local demo API contract

All routes use `/api`. JSON errors use FastAPI `detail`. All mutations are transactional and audited. Synthetic business date defaults to `2026-10-21` (`DEMO_DATE` can override); timestamps are UTC. Prices are INR. No real purchases, messages or calls occur.

## Resources

- `GET /health`: `{status, mode, voice: {provider, available, message}}`
- `GET /dashboard`: `{metrics: {total_jobs, awaiting_recovery, awaiting_parts, awaiting_confirmation, resolved, recovery_rate}, status_counts: [{status,count}], recent_activity: Activity[], demo_date}`
- `GET /jobs`: `Job[]`
- `GET /jobs/{id}`: `JobDetail`
- `GET /inventory`: `{parts: Part[], suppliers: Supplier[], quotes: Quote[]}`
- `GET /activity`: `Activity[]`, optional `job_id` query

`Job`: `{id, customer_name, equipment_id, equipment_name, technician_id, technician_name, issue, required_part, priority, status, next_action, expected_revisit, is_demo}`.

`Activity`: `{id, job_id, event_type, details, created_at}`. `details` is a JSON object.

`Part`: `{id,name,stock,unit_cost}`. `Supplier`: `{id,name,response_status}`. `Quote`: `{supplier_id,supplier_name,part_name,available,unit_price,delivery_days,response_status}`.

`JobDetail` extends `Job` with `{history: Activity[], equipment_history: object[], plan: object|null, approval: {id,status,details}|null, order: object|null, revisit: {id,status,slots: string[],selected_slot,customer_confirmed,technician_confirmed}|null}`.

## Controlled workflow

- `POST /agent/report` body `{message: string, job_id?: string}` returns `{message, needs_clarification: boolean, extracted: object, candidates: Job[], job: JobDetail|null, tool_calls: [{name,arguments,result}]}`. Job/part ambiguity must return clarification without job mutation. Repeat report must not duplicate plan/order/booking. A bare job ID can use stored diagnosis for the demo. Tool calls represent actual functions executed.
- `POST /approvals/{id}` body `{decision: "approved"|"rejected", actor: string}`. Approval creates an idempotent simulated order and proposes a revisit; rejection escalates with no order. Returns `JobDetail`.
- `POST /jobs/{id}/revisit/confirm` body `{selected_slot: string, customer_confirmed: boolean, technician_confirmed: boolean, actor: string}`. Both confirmations required before scheduled; partial confirmations persist. Returns `JobDetail`.
- `POST /jobs/{id}/start-repair` body `{actor: string}`. Requires scheduled revisit. Returns `JobDetail`.
- `POST /jobs/{id}/complete` body `{confirmed: boolean, evidence: string, actor: string}`. Requires repair in progress and explicit affirmative confirmation with evidence. Returns `JobDetail`.
- `POST /jobs/{id}/escalate` body `{reason: string, actor: string}`. Human takeover; returns `JobDetail`.
- `POST /followups/run` body optional `{}`. Returns `{escalated: string[], checked: number}`. Checks due actions without inventing communications. Clock-injectable for tests.
- `POST /demo/reset` body `{confirm: true}` restores deterministic fixtures, demo only. Returns `{message}`.

One controlled recovery workflow; no autonomous multi-agent implementation. Local default uses a clearly labeled deterministic report interpreter and SQLite. Bolna adapter boundary is disabled until phase two. UI connects only through these API routes, with Next.js proxy to backend.
