# Failed Repair Recovery Agent

A working **synthetic-data demo** of the workflow after a technician cannot complete a repair: report → identify job → inspect history/inventory → compare simulated suppliers → approval → simulated order → proposed revisit → confirmations → verified completion.

Phase one intentionally works without real customers, a technician database, API keys or Bolna. The dashboard uses the backend's persisted state. The report interpreter is deterministic and limited to the demo vocabulary; it is not a live LLM. Bolna/LLM credentials are reserved for phase two and do not enable live calls in this version.

## Stack and architecture

- **Next.js / TypeScript / Tailwind** dashboard, repair jobs, recovery agent, inventory/suppliers and activity timeline.
- **FastAPI / Pydantic** validated API and one controlled recovery workflow.
- **SQLite** local persistent database with reproducible fixtures, transactional state changes and audit history.
- **Mock supplier adapter** with actual backend lookups, prices and delivery estimates. All orders are explicitly simulated.

```text
Next.js dashboard ── /api proxy ── FastAPI
                                     │
                             validated workflow
                       ┌─────────────┼──────────────┐
                  report parser   backend tools   mock suppliers
                                     │
                              SQLite + audit log
```

The browser cannot set arbitrary job statuses. Approval, appointment confirmation, starting a repair and completion each have separate validated endpoints. A repeated approval cannot create a second order. An appointment needs both customer and technician confirmation. Resolution needs an explicit completion report and evidence after repair has started.

## Run locally

Requirements: Python 3.10+ and **Node.js 20.9+** (Node 22 recommended). Next.js 16's requirement is documented in the [official installation guide](https://nextjs.org/docs/app/getting-started/installation).

If your machine has an older Node version, install a workspace-local runtime without changing the system runtime:

```bash
npm install --prefix .tools/node node@22
export PATH="$PWD/.tools/node/node_modules/.bin:$PATH"
```

The development script detects this local runtime automatically.

From the repository root:

```bash
python -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.txt
cd frontend
npm ci
cd ..
cp .env.example .env
bash scripts/dev.sh
```

Open **http://localhost:3000**. Interactive API documentation: **http://localhost:8000/docs**. The database is seeded on first startup; subsequent restarts retain changes.

Alternatively, use two terminals:

```bash
# Terminal 1, from repository root
cd backend
../.venv/bin/python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

```bash
# Terminal 2, from repository root
cd frontend
npm run dev
```

The separate-terminal commands use defaults; export custom environment variables before starting each process. `scripts/dev.sh` loads the root `.env` for both processes. Environment files must contain only trusted shell assignments.

## Try the demo

1. Open **Recovery agent** and run the restaurant example with `JOB-1001`.
2. Check the extracted details, actual tool results, inventory shortage and supplier comparison.
3. Approve the ₹1,200 simulated order. Until approval, no order exists.
4. Choose a proposed revisit slot. Record the simulated customer's confirmation and the technician's confirmation; one confirmation alone leaves the appointment pending.
5. Start the repair, then explicitly confirm completion with evidence, for example: “Replaced compressor relay; refrigerator reached 4°C and passed a 20-minute cooling test.”
6. Verify the resolved job, metrics and activity history. Reset the synthetic demo to replay it.

See [the walkthrough](docs/demo-walkthrough.md) and [API contract](docs/api-contract.md). All customer/technician confirmations are operator-recorded simulations; the application does not claim to contact anyone.

## Data and reproducibility

Fixtures include 5 fictional businesses, 3 technicians, 10 jobs, 12 spare parts, 3 suppliers, equipment history and appointment windows. The business date defaults to **21 October 2026**, making “tomorrow” **22 October 2026**. This is a simulation date, not the viewer's current date. Set `DEMO_DATE` before first startup to choose another dated scenario. To change an existing database's date, stop the backend, export the new `DEMO_DATE`, run the CLI seed command with `--reset`, and restart. The dashboard reset preserves the database's scenario date.

Use the dashboard's explicit reset control, or:

```bash
cd backend
../.venv/bin/python -m app.seed --reset
```

Reset deletes and recreates the synthetic database contents. Use a separate database path for each test/deployment. Do not point this demo at real customer data.

The synthetic clock begins at 09:00 UTC on the scenario date and advances while the process runs. A restart resumes from the latest persisted activity; time spent with the process stopped does not advance the simulation. The dashboard exposes the scenario date, and follow-up deadlines use this simulated clock. The dashboard reset also rewinds that clock.

## Configuration

| Variable                              | Purpose                                                                                                                       |
| ------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| `DATABASE_PATH` | SQLite file path for local development; default is the backend's local `data/demo.sqlite3`. Ignored when `DATABASE_URL` is set. |
| `DATABASE_URL` | Optional PostgreSQL/Supabase connection string. When set, it is used instead of `DATABASE_PATH`. |
| `DEMO_DATE`                         | Fixed scenario date, default `2026-10-21`.                                                                                  |
| `DEMO_MODE`                         | This application is a demo. Non-demo operation is unsupported.                                                                |
| `BACKEND_URL`                       | Next.js server-side API proxy destination; default `http://127.0.0.1:8000`.                                                 |
| `CORS_ORIGINS`                      | Allowed browser origins for direct API access. Normal UI traffic uses the same-origin Next.js proxy.                          |
| `BOLNA_API_KEY`, `BOLNA_AGENT_ID` | Optional server-side Bolna credentials. When both are set, `/api/voice/call` can place a call; calls consume Bolna credits. |
| `VOICE_TOOL_SECRET` | Required shared secret for Bolna custom tools that retrieve a job and submit a technician report. |
| `LLM_API_KEY`                       | Unused phase-two placeholder; reports use the mock interpreter.                                                               |

No secret belongs in a `NEXT_PUBLIC_` variable. The frontend reads no provider credentials.

### Bolna call workflow

The backend supports two Bolna custom tools:

- `GET /api/voice/tools/job/{job_id}` retrieves the job context.
- `POST /api/voice/tools/report` submits `{ "job_id": "JOB-1001", "message": "..." }` and runs the existing recovery workflow.

Configure these URLs in the Bolna agent's custom tools using the Render backend URL and send
`X-Voice-Tool-Secret: <VOICE_TOOL_SECRET>`. Instruct the agent to ask for the exact job ID,
collect the first-visit findings and required part, read back the report, and call the report
tool only after the caller confirms it. The tool creates the plan and pending approval; it does
not approve expenditure, schedule a revisit without both confirmations, or mark a repair complete.

## Verification

```bash
cd backend
../.venv/bin/python -m pytest -q
cd ../frontend
npm run typecheck
npm run build
```

The backend tests cover the complete recovery scenario and critical negative paths, including approval/confirmation gates and duplicate requests. Browser verification details are recorded in [verification](docs/verification.md).

For the browser scenario, start a disposable demo instance, then run:

```bash
cd e2e
npm ci
npx playwright install chromium
ALLOW_DEMO_RESET=yes npm test
```

This test resets demo progress, uses the actual UI through resolution, verifies persistence after a reload and checks the mobile layout. It saves screenshots under `test-results/`. Set `BASE_URL` for another local port or `CHROMIUM_PATH` to use an installed Chromium executable. Do not run it against demo state you want to retain.

## Deploy the demo

```bash
docker compose up --build
```

The frontend is on port 3000 and backend on 8000, both bound to loopback by default. SQLite persists in the `demo-data` volume. The frontend build receives `BACKEND_URL=http://backend:8000`; rebuild when changing its proxy destination because Next.js rewrites are captured at build time.

For a hosted demo, deploy the two containers on a private network with a persistent backend volume and put an authenticated HTTPS reverse proxy in front of the frontend. This demo has no account system or authorization layer; its operator controls and reset endpoint must remain private. Single-backend SQLite deployment is supported; do not scale to independent replicas sharing a local file.

### Supabase/PostgreSQL deployment

The backend supports SQLite locally and PostgreSQL/Supabase when `DATABASE_URL` is
set. Create a Supabase project, open **Connect**, copy a PostgreSQL connection
string, and add `sslmode=require` if it is not already included. Keep the
password URL-encoded and store the complete value only in Render's environment
variables:

```env
DATABASE_URL=postgresql://postgres.<project-ref>:<password>@<host>:5432/postgres?sslmode=require
```

On Render, set `DATABASE_URL` and redeploy. It takes precedence over
`DATABASE_PATH`; remove `DATABASE_PATH` from Render to avoid ambiguity. The
application initializes the schema and seeds the deterministic fixture data on
the first startup. To intentionally reset the shared database, run the seed
command as a one-off job or locally with the same `DATABASE_URL`:

```bash
cd backend
../.venv/bin/python -m app.seed --reset
```

Do not run reset against a database containing data you need to keep. Supabase
is now the shared persistent database for Render and any local process configured
with the same URL; local SQLite remains the default when `DATABASE_URL` is absent.

Sites hosting runs server code in Cloudflare Workers and cannot run this Python service as supplied. The requested FastAPI + Next.js architecture is therefore delivered as a local/container application instead of publishing a disconnected dashboard.

Supabase PostgreSQL is the next database deployment option. This version uses SQLite-specific SQL and is **not** a drop-in PostgreSQL adapter. A migration, connection pooling and concurrency verification are needed before switching databases.

## Phase two: live integrations

Once this workflow is accepted, replace the mock report interpreter with schema-validated LLM extraction and add Bolna transport. Feed transcripts into the same controlled workflow; keep approval/order/booking/completion gates server-side. Implement authenticated and signed webhook handling, replay protection, verified provider delivery status, and transcript consent before enabling real calls. Merely adding credentials does not activate these integrations.

Follow-up checks are explicit and observable; this demo does not run an unattended worker. A production deployment would schedule the follow-up function and add authenticated operator identities, persistent task scheduling and real supplier/customer adapters.
