# Restaurant refrigerator recovery demo

Run the backend and dashboard using the root README. Every customer, technician,
The seeded businesses, jobs, inventory and suppliers are sample records. The
default scenario date is **21 October 2026**. Orders and participant
confirmations are simulated operator inputs. Optional Bolna call and workflow
tool endpoints are available when configured; supplier purchases and participant
confirmations remain simulated.

## Complete one recovery

1. Choose **Reset demo** in the dashboard footer, then **Yes, reset demo**. Open **JOB-1001**, the
   commercial refrigerator at **Demo Restaurant**. It starts at **Failed First
   Visit**, with a faulty compressor relay and no replacement in stock.
2. Open **Recovery agent**, paste the following, and choose **Send report**:

   > JOB-1001: I visited Demo Restaurant to repair its commercial refrigerator.
   > The compressor relay is faulty and I do not have the replacement part.
   > This is urgent.

3. Inspect the extracted fields, executed tool calls and recovery plan. The
   workflow retrieves the job and equipment history, checks inventory, compares
   simulated quotes and recommends the available **Supplier A** option at
   **₹1,200**, with an estimated next-day delivery. The job is now **Awaiting
   Approval**. There must be no order or confirmed revisit yet.
4. Choose **Open job & next actions**, then **Approve simulated order**. The system creates one
   **simulated** order and proposes available revisit windows for **22 October
   2026**. The job remains **Awaiting Revisit Confirmation**.
5. Select a proposed slot, check **Customer confirmed**, and choose **Save
   confirmations**. The job must remain pending. Check **Technician confirmed**
   for that same slot and save again;
   the job becomes **Revisit Scheduled**.
6. Choose **Start repair**. The status becomes **Repair In Progress** and the
   completed-job count must remain unchanged.
7. Enter **Completion evidence** such as:

   > Replaced compressor relay; refrigerator passed the cooling test.

   Check the explicit completion confirmation, then choose **Confirm repair
   completion**. The job becomes **Resolved**. Check its history, the activity timeline and the
   dashboard's completed-job count and recovery rate.

## Check the important failure branches

- **Approval rejected:** reset, report `JOB-1001`, then reject approval. The job
  escalates with no order or revisit.
- **Internal inventory:** reset and report `JOB-1002`. Its oven thermostat is in
  stock; the workflow proposes a revisit without a supplier order or expenditure
  approval.
- **No available supplier:** reset and report `JOB-1003`. Its fan motor cannot be
  sourced from the simulated suppliers; the job escalates for human attention.
- **Ambiguous report:** submit “The commercial refrigerator at Demo Restaurant
  needs a compressor relay” without a job ID. Two jobs match, so the agent asks
  for clarification and changes neither job.
- **Unknown part:** submit “JOB-1001: the flux capacitor is faulty; I need a
  replacement flux capacitor.” The agent asks for clarification instead of
  replacing the diagnosis with a known part.
- **Retries:** submit the same report or approval again. The existing plan,
  approval, simulated order and revisit are reused.
- **Human takeover:** use escalation with a reason. Pending approval can no
  longer create an order for the escalated job.

The follow-up endpoint checks overdue pending actions against an injectable
clock. Automated tests advance the clock by two days and verify escalation
without claiming a call or message was sent. Repeated follow-up runs do not
duplicate the escalation. Expired approval requests cannot create orders, and
past appointment proposals cannot receive a new confirmation. If approval arrives
after midnight but within its valid window, the order updates the delivery
estimate while keeping the reviewed price and original estimate in its audit data.

## API verification

The frontend uses the API in [api-contract.md](api-contract.md). To run the
isolated HTTP tests from the repository root:

```bash
.venv/bin/python -m pytest backend/tests -q
```

The tests use a fresh temporary SQLite database and a fixed clock for each case.
They cover the full recovery, approval and confirmation gates, explicit
completion evidence, duplicate and concurrent approvals, deterministic reset,
inventory sourcing, supplier failure, ambiguous reports, invalid requests and
overdue escalation. They also verify quantity validation, stock reservations,
customer and technician booking conflicts, no compatible appointment windows,
the approved price remaining fixed if the supplier quote changes, expired
actions, and confirmation retries after the appointment time. No external
account or API credential is required.
