export type RecordData = Record<string, unknown>;
export interface Activity { id: string | number; job_id: string; event_type: string; details: RecordData; created_at: string }
export interface Job { id: string; customer_name: string; equipment_id: string; equipment_name: string; technician_id: string; technician_name: string; issue: string; required_part: string; priority: string; status: string; next_action: string; expected_revisit: string | null; is_demo: boolean }
export interface JobDetail extends Job {
  history: Activity[];
  equipment_history: RecordData[];
  plan: RecordData | null;
  approval: { id: string; status: string; details: RecordData } | null;
  order: RecordData | null;
  revisit: { id: string; status: string; slots: string[]; selected_slot: string | null; customer_confirmed: boolean; technician_confirmed: boolean } | null;
}
export interface Dashboard { metrics: { total_jobs: number; awaiting_recovery: number; awaiting_parts: number; awaiting_confirmation: number; resolved: number; recovery_rate: number }; status_counts: { status: string; count: number }[]; recent_activity: Activity[]; demo_date: string; metric_note?: string }
export interface Part { id: string; name: string; stock: number; unit_cost: number }
export interface Supplier { id: string; name: string; response_status: string }
export interface Quote { supplier_id: string; supplier_name: string; part_name: string; available: boolean; unit_price: number; delivery_days: number; response_status: string }
export interface Inventory { parts: Part[]; suppliers: Supplier[]; quotes: Quote[] }
export interface Health { status: string; mode: string; voice: { provider: string; available: boolean; message: string } }
export interface AgentResult { message: string; needs_clarification: boolean; extracted: RecordData; candidates: Job[]; job: JobDetail | null; tool_calls: { name: string; arguments: RecordData; result: unknown }[] }
export interface Snapshot { dashboard: Dashboard; jobs: Job[]; inventory: Inventory; activity: Activity[]; health: Health }

export async function api<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`/api${path}`, { method: body === undefined ? "GET" : "POST", headers: { "Content-Type": "application/json" }, cache: "no-store", ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = data.detail;
    throw new Error(typeof detail === "string" ? detail : Array.isArray(detail) ? detail.map((item: { msg?: string }) => item.msg || "Invalid request").join("; ") : `Request failed (${response.status}). Check that the backend is running.`);
  }
  return data as T;
}
export async function fetchSnapshot(): Promise<Snapshot> {
  const [dashboard, jobs, inventory, activity, health] = await Promise.all([api<Dashboard>("/dashboard"), api<Job[]>("/jobs"), api<Inventory>("/inventory"), api<Activity[]>("/activity"), api<Health>("/health")]);
  return { dashboard, jobs, inventory, activity, health };
}
export function humanize(value: string): string { return value.replace(/[_-]/g, " ").toLowerCase().replace(/\b\w/g, (s) => s.toUpperCase()); }
export function money(value: number): string { return new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 }).format(value); }
export function formatDate(value: string | null, withTime = false): string {
  if (!value) return "Not scheduled";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("en-IN", { timeZone: "Asia/Kolkata", day: "numeric", month: "short", ...(withTime ? { hour: "2-digit", minute: "2-digit" } : { year: "numeric" }) }).format(date);
}
export function errorMessage(error: unknown): string { return error instanceof Error ? error.message : "Something went wrong. Please try again."; }
