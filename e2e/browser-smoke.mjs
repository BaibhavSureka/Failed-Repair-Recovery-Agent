import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
import { chromium } from 'playwright';

// Run against a disposable demo instance: this test explicitly resets its fixtures.
const baseURL = process.env.BASE_URL || 'http://127.0.0.1:3000';
if (process.env.ALLOW_DEMO_RESET !== 'yes') {
  throw new Error('This test resets synthetic demo progress. Set ALLOW_DEMO_RESET=yes for a disposable demo instance.');
}
const browser = await chromium.launch({
  headless: true,
  ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}),
  args: ['--no-sandbox'],
});
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
const page = await context.newPage();
const errors = [];
page.on('pageerror', (error) => errors.push(error.message));
await mkdir('../test-results', { recursive: true });

async function read(path) {
  const response = await context.request.get(`${baseURL}/api${path}`);
  assert.equal(response.status(), 200, await response.text());
  return response.json();
}

async function action(path, click) {
  const [response] = await Promise.all([
    page.waitForResponse((response) => response.url().endsWith(`/api${path}`) && response.request().method() === 'POST'),
    click(),
  ]);
  assert.equal(response.status(), 200, await response.text());
  // Each UI mutation reloads the dashboard; wait for that before the next action.
  await page.getByRole('button', { name: 'Refresh data' }).waitFor();
  return response.json();
}

try {
  const health = await read('/health');
  assert.equal(health.voice.available, false);
  const reset = await context.request.post(`${baseURL}/api/demo/reset`, { data: { confirm: true } });
  assert.equal(reset.status(), 200, await reset.text());
  await page.goto(baseURL, { waitUntil: 'networkidle' });
  await page.getByText('Total repair jobs', { exact: true }).waitFor();
  const before = (await read('/dashboard')).metrics;
  assert.equal(before.total_jobs, 10);
  await page.screenshot({ path: '../test-results/dashboard-desktop.png', fullPage: true });
  await page.getByRole('button', { name: 'Open the recovery scenario' }).click();
  const response = await action('/agent/report', () => page.getByRole('button', { name: 'Send report' }).click());
  assert.equal(response.needs_clarification, false);
  assert.equal(response.job.status, 'AWAITING_APPROVAL');
  assert.equal(response.job.order, null);
  await page.getByRole('button', { name: 'Open job & next actions' }).click();
  const detail = page.getByRole('region', { name: 'Details for JOB-1001' });
  await detail.waitFor();
  const approved = await action(`/approvals/${response.job.approval.id}`, () => page.getByRole('button', { name: 'Approve simulated order' }).click());
  assert.equal(approved.status, 'AWAITING_REVISIT_CONFIRMATION');
  await page.getByLabel('Customer confirmed').check();
  await action('/jobs/JOB-1001/revisit/confirm', () => page.getByRole('button', { name: 'Save confirmations' }).click());
  const partial = await read('/jobs/JOB-1001');
  assert.equal(partial.status, 'AWAITING_REVISIT_CONFIRMATION');
  assert.equal(partial.revisit.customer_confirmed, true);
  assert.equal(partial.revisit.technician_confirmed, false);
  await page.getByLabel('Technician confirmed').check();
  await action('/jobs/JOB-1001/revisit/confirm', () => page.getByRole('button', { name: 'Save confirmations' }).click());
  await action('/jobs/JOB-1001/start-repair', () => page.getByRole('button', { name: 'Start repair', exact: true }).click());
  await page.getByLabel('Completion evidence').fill('Replaced compressor relay; refrigerator reached 4°C and passed a 20-minute cooling test.');
  await page.getByLabel('I explicitly confirm this demo repair was completed and verified.').check();
  await action('/jobs/JOB-1001/complete', () => page.getByRole('button', { name: 'Confirm repair completion' }).click());
  await page.getByText('A successful recovery.', { exact: true }).waitFor();
  const completed = await read('/jobs/JOB-1001');
  assert.equal(completed.status, 'RESOLVED');
  assert.equal((await read('/dashboard')).metrics.resolved, before.resolved + 1);
  await page.reload({ waitUntil: 'networkidle' });
  await page.getByRole('button', { name: 'Open JOB-1001', exact: true }).click();
  await page.getByText('A successful recovery.', { exact: true }).waitFor();
  await page.getByRole('button', { name: 'Inventory & suppliers', exact: true }).click();
  await page.getByText('Parts on the shelf', { exact: true }).waitFor();
  await page.getByRole('button', { name: 'Activity timeline', exact: true }).click();
  await page.getByText('Recovery audit trail', { exact: true }).waitFor();
  await page.getByLabel('Filter activity by job').selectOption('JOB-1001');
  await page.getByRole('button', { name: 'Overview', exact: true }).click();
  await page.screenshot({ path: '../test-results/dashboard-completed.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: '../test-results/dashboard-mobile.png', fullPage: true });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true, 'Mobile page overflows horizontally');
  assert.deepEqual(errors, [], 'Browser emitted uncaught runtime errors');
  console.log(JSON.stringify({ result: 'passed', job: completed.id, status: completed.status, resolved_before: before.resolved, resolved_after: before.resolved + 1, browser_errors: errors, viewports: ['1440×1000', '390×844'] }, null, 2));
} finally {
  await browser.close();
}
