# Failed Repair Recovery Agent

A repair recovery workflow for cases where a technician cannot complete a repair: report → identify job → inspect history/inventory → compare supplier options → approval → order record → proposed revisit → confirmations → verified completion. An optional Bolna integration can place calls and submit technician reports through protected backend tools.

The dashboard uses persisted backend state. Report interpretation is deterministic and limited to supported phrases; it does not use a live LLM. Supplier orders and customer/technician confirmations are recorded as simulated actions. Bolna calls are optional, require server-side credentials, and consume provider credits; configuring Bolna does not connect supplier ordering or participant confirmations.

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

### Check the project and prepare a submission

From the repository root, run the automated backend and frontend checks:

```bash
bash scripts/verify.sh
```

To verify the running app manually, visit `http://localhost:3000`, open **Recovery agent**, and run the `JOB-1001` restaurant example. Confirm that the report creates a recovery plan, approval is required before a simulated order is placed, both customer and technician confirmations are required for a revisit, and completion requires evidence. The API health endpoint is `http://localhost:8000/api/health`.

Before submitting, include the repository source and README, plus any deliverables your course or reviewer requests (for example, a short recording or screenshots). List the checks you ran and their results, and state that supplier orders and participant confirmations are simulated. Do not include `.env`, API keys, `VOICE_TOOL_SECRET`, local database files, `.venv`, `node_modules` or generated test results. The repository's `.gitignore` excludes local secrets and generated state.

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

## Try the workflow

1. Open **Recovery agent** and run the restaurant example with `JOB-1001`.
2. Check the extracted details, actual tool results, inventory shortage and supplier comparison.
3. Approve the ₹1,200 simulated order. Until approval, no order exists.
4. Choose a proposed revisit slot. Record the simulated customer's confirmation and the technician's confirmation; one confirmation alone leaves the appointment pending.
5. Start the repair, then explicitly confirm completion with evidence, for example: “Replaced compressor relay; refrigerator reached 4°C and passed a 20-minute cooling test.”
6. Verify the resolved job, metrics and activity history. Reset the sample workspace to replay it.

See [the walkthrough](docs/demo-walkthrough.md) and [API contract](docs/api-contract.md). All customer/technician confirmations are operator-recorded simulations; the application does not claim to contact anyone.

## Data and reproducibility

The seeded sample workspace contains 5 fictional businesses, 3 technicians, 10 jobs, 12 spare parts, 3 suppliers, equipment history and appointment windows. The scenario date defaults to **21 October 2026**, making “tomorrow” **22 October 2026**. Set `DEMO_DATE` before first startup to choose another scenario date. To change an existing database's date, stop the backend, export the new `DEMO_DATE`, run the seed command with `--reset`, and restart. The dashboard reset preserves the configured scenario date.

Use the dashboard's explicit reset control, or:

```bash
cd backend
../.venv/bin/python -m app.seed --reset
```

Reset deletes and recreates the seeded sample records. Use a separate database path for each test/deployment. Do not point this workspace at real customer data.

The scenario clock begins at 09:00 UTC on the configured date and advances while the process runs. A restart resumes from the latest persisted activity; time spent with the process stopped does not advance the clock. The dashboard exposes the scenario date, and follow-up deadlines use this clock. Reset rewinds it.

## Configuration

| Variable                              | Purpose                                                                                                                       |
| ------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| `DATABASE_PATH` | SQLite file path for local development; default is the backend's local `data/demo.sqlite3`. Ignored when `DATABASE_URL` is set. |
| `DATABASE_URL` | Optional PostgreSQL/Supabase connection string. When set, it is used instead of `DATABASE_PATH`. |
| `DEMO_DATE`                         | Fixed scenario date, default `2026-10-21`.                                                                                  |
| `DEMO_MODE`                         | Must remain `true`; other modes are unsupported.                                                                |
| `BACKEND_URL`                       | Next.js server-side API proxy destination; default `http://127.0.0.1:8000`.                                                 |
| `CORS_ORIGINS`                      | Allowed browser origins for direct API access. Normal UI traffic uses the same-origin Next.js proxy.                          |
| `BOLNA_API_KEY`, `BOLNA_AGENT_ID` | Optional server-side Bolna credentials. When both are set, `/api/voice/call` can place a call; calls consume Bolna credits. |
| `VOICE_TOOL_SECRET` | Required shared secret for Bolna custom tools that retrieve a job and submit a technician report. |
| `LLM_API_KEY`                       | Reserved, unused; report interpretation is deterministic.                                                               |

No secret belongs in a `NEXT_PUBLIC_` variable. The frontend reads no provider credentials.

### Bolna voice integration

The backend supports two Bolna custom tools:

- `GET /api/voice/tools/job?job_id=JOB-1001` retrieves the job context. A path variant, `/api/voice/tools/job/{job_id}`, is also available.
- `POST /api/voice/tools/report` submits `{ "job_id": "JOB-1001", "message": "..." }` and runs the existing recovery workflow.

Configure the GET and POST URLs in the Bolna agent's custom tools using your backend URL, and send `X-Voice-Tool-Secret` with the same `VOICE_TOOL_SECRET` configured on the backend. For the report tool, send a JSON body with `job_id`, `message`, and optionally `actor`. Ask the agent to confirm the normalized job ID, collect the first-visit findings and required part, read the report back, and submit only after the caller confirms it. Keep `message` to one line (separate details with commas); multiline values have caused malformed JSON in some custom-tool configurations. The tool creates a plan and pending approval; it does not approve expenditure, schedule a revisit without both confirmations, or mark a repair complete.

For Render, set `VOICE_TOOL_SECRET` and any optional Bolna credentials as private environment variables, then redeploy. Configure the exact same shared secret in each Bolna tool's `X-Voice-Tool-Secret` header. Never paste the secret into prompts, source control or screenshots. If a tool returns job-ID validation errors, check that its saved URL uses the query-string form above and that the updated tool configuration was saved.

## Verification

```bash
cd backend
../.venv/bin/python -m pytest -q
cd ../frontend
npm run typecheck
npm run build
```

The backend tests cover the complete recovery scenario and critical negative paths, including approval/confirmation gates and duplicate requests. Browser verification details are recorded in [verification](docs/verification.md).

For the browser scenario, start a disposable local instance, then run:

```bash
cd e2e
npm ci
npx playwright install chromium
ALLOW_DEMO_RESET=yes npm test
```

This test resets workspace progress, uses the actual UI through resolution, verifies persistence after a reload and checks the mobile layout. It saves screenshots under `test-results/`. Set `BASE_URL` for another local port or `CHROMIUM_PATH` to use an installed Chromium executable. Do not run it against workspace state you want to retain.

## Deploy

```bash
docker compose up --build
```

The frontend is on port 3000 and backend on 8000, both bound to loopback by default. SQLite persists in the `demo-data` volume. The frontend build receives `BACKEND_URL=http://backend:8000`; rebuild when changing its proxy destination because Next.js rewrites are captured at build time.

For hosted use, deploy the two containers on a private network with a persistent backend volume and put an authenticated HTTPS reverse proxy in front of the frontend. The application has no account system or authorization layer; its operator controls and reset endpoint must remain private. Single-backend SQLite deployment is supported; do not scale to independent replicas sharing a local file.

### PostgreSQL/Supabase deployment

The backend includes a PostgreSQL driver and SQL compatibility layer. To use
PostgreSQL, create a Supabase project, open **Connect**, copy a connection
string, and add `sslmode=require` if needed. URL-encode special characters in
the password and store the connection string only in your hosting environment:

```env
DATABASE_URL=postgresql://postgres.<project-ref>:<password>@<host>:5432/postgres?sslmode=require
```

Set `DATABASE_URL` on Render and redeploy. It takes precedence over
`DATABASE_PATH`. On first startup, the application creates the schema and seeds
the sample records if the database has no seed marker. To reset the shared
database, run the seed command as a one-off job or locally with the same URL:

```bash
cd backend
../.venv/bin/python -m app.seed --reset
```

Every process using the same URL shares that database. SQLite remains the local
default. The automated backend suite uses SQLite; run deployment-specific smoke
checks against a disposable PostgreSQL database before relying on that setup.
Never reset a database containing data you need to keep.

Cloudflare Pages/Workers cannot run the Python service as supplied. Deploy the
frontend and FastAPI backend to hosting that supports both runtimes, or use the
provided containers.

## Integration status and boundaries

Bolna support is implemented as an optional provider connection:

- `POST /api/voice/call` starts a call when `BOLNA_API_KEY` and `BOLNA_AGENT_ID` are configured. Calls consume provider credits.
- `GET /api/voice/executions/{execution_id}` retrieves a Bolna execution.
- `GET /api/voice/tools/job` (or `/job/{job_id}`) and `POST /api/voice/tools/report` support custom tools. Both require `VOICE_TOOL_SECRET`.

Report interpretation remains deterministic; live LLM extraction is not
implemented and `LLM_API_KEY` is unused. Supplier quotes/orders and
customer/technician confirmations are simulated. Follow-up checks run only when
an operator uses the dashboard action or calls `POST /api/followups/run`; there
is no background scheduler.

This application has no operator accounts or general API authorization. The
voice-tool secret protects only the two custom-tool routes. Voice call/execution
routes and operator routes do not require user authentication. Signed webhook
ingestion, replay protection and transcript-consent handling are not
implemented. Keep deployments private or behind an authenticated HTTPS access
layer; do not use real customer data or enable real calls until the necessary
controls are implemented and reviewed.
