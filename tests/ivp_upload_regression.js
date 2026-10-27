// Regression test for the IVP Entry Screener's "uploaded Sensibull file"
// data path (the Google Sheet route in initIvpScreener(), index.html).
//
// Bug this guards against: ivpRenderOptionsSignal() was originally called
// once at the top of initIvpScreener(), before `ivpData` is populated from
// the Google Sheet's rows later in the same function. The IVP% column in
// the Signals > CALL SELL / PUT SELL tables is built from `ivpData.entries`,
// so it rendered "—" for every row on the real upload path even though
// Supertrend/RSI14 in the same tables (sourced from separate dicts set
// earlier) worked fine. Fixed by calling ivpRenderOptionsSignal() again
// right after ivpData is assigned from the sheet response.
//
// This test exercises exactly that path — mocks the Google Sheet endpoint
// to return real rows (simulating "user has uploaded"), not the site's
// own weekly options_snapshot.json fallback (which never had this bug,
// since ivpData is set before the render call on that path).
//
// Run: node tests/ivp_upload_regression.js
// (If `require('playwright')` fails from the repo root, re-run with
//  NODE_PATH=/opt/node22/lib/node_modules node tests/ivp_upload_regression.js
//  or wherever this machine's global Playwright install lives.)

const { chromium } = require('playwright');
const { spawn } = require('child_process');
const path = require('path');

const REPO_ROOT = path.resolve(__dirname, '..');
const PORT = process.env.IVP_TEST_PORT || 8899;
const BASE_URL = `http://127.0.0.1:${PORT}/index.html`;

const MOCK_SHEET_ROWS = [
  // symbol, IVP, RSI, Supertrend color -> expected tab(s) and expected IVP% text
  { symbol: '360ONE.NS', ivPercentile: 42, rsi: 30.3, stColor: 'red' },   // CALL SELL only (ST red)
  { symbol: 'ABB.NS',     ivPercentile: 78, rsi: 36.6, stColor: 'red' },  // CALL SELL only
  { symbol: 'ADANIENT.NS',ivPercentile: 15, rsi: 72.0, stColor: 'green'},// both (RSI>70 -> call, ST green -> put)
];

function fail(msg) {
  console.error(`FAIL: ${msg}`);
  process.exitCode = 1;
}

function startServer() {
  return new Promise((resolve, reject) => {
    const srv = spawn('python3', ['-m', 'http.server', String(PORT)], { cwd: REPO_ROOT, stdio: 'ignore' });
    srv.on('error', reject);
    // Give it a moment to bind, then assume it's up (curl-style check omitted
    // for a plain-Node script; Playwright's goto will retry/fail loudly if not).
    setTimeout(() => resolve(srv), 800);
  });
}

async function run() {
  const server = await startServer();
  let browser;
  let failed = false;
  try {
    browser = await chromium.launch();
    const page = await browser.newPage({ viewport: { width: 1400, height: 1000 } });
    const pageErrors = [];
    page.on('pageerror', e => pageErrors.push(e.message));

    const sheetRows = MOCK_SHEET_ROWS.map(r => ({
      symbol: r.symbol, futurePrice: 1000, futurePercentChange: 0,
      atmIv: 30, atmIvChange: 0, ivPercentile: r.ivPercentile,
      eventDate: '', volumeMultiple: 1, futureOiPercentChange: 0, pcr: 1, maxPain: 1000,
    }));

    // Only ONE route for the Google Sheet endpoint — a broader
    // **/macros/s/** glob would also match this URL and (depending on
    // registration order) could shadow it with empty rows, silently
    // falling back to the snapshot path and defeating the test.
    await page.route('**/exec?type=ivp**', r => r.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ success: true, generatedAt: new Date().toISOString(), rows: sheetRows }),
    }));
    await page.route('**/market_screens.json*', r => r.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ rsi_all: MOCK_SHEET_ROWS.map(r => ({ symbol: r.symbol, rsi14: r.rsi })), generated_at: new Date().toISOString() }),
    }));
    await page.route('**/supertrend.json*', r => r.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ entries: MOCK_SHEET_ROWS.map(r => ({ symbol: r.symbol, close: 1000, color: r.stColor })), generated_at: new Date().toISOString() }),
    }));
    await page.route('**/supertrend_full.json*', r => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ entries: [], generated_at: new Date().toISOString() }) }));
    await page.route('**/market_screens_full.json*', r => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ rsi_all: [], week52_all: [], generated_at: new Date().toISOString() }) }));

    await page.goto(BASE_URL, { waitUntil: 'load' });
    await page.click('#nav-ivp');
    await page.waitForTimeout(1500);
    await page.click('#subnav-ivp button[data-subtab="signals"]');
    await page.waitForTimeout(200);

    await page.click('#subnav-ivpsignals button[data-subtab="call"]');
    await page.waitForTimeout(200);
    const callText = await page.$eval('#ivp-opt-body-call', el => el.textContent.replace(/\s+/g, ' '));

    await page.click('#subnav-ivpsignals button[data-subtab="put"]');
    await page.waitForTimeout(200);
    const putText = await page.$eval('#ivp-opt-body-put', el => el.textContent.replace(/\s+/g, ' '));

    // The actual regression check: IVP% must be the real uploaded value,
    // not "—". Checking a literal "42%"/"78%"/"15%" substring is a blunt
    // but direct way to catch "column always blank" regressions.
    const checks = [
      ['CALL SELL table contains 360ONE at 42%', callText.includes('42%')],
      ['CALL SELL table contains ABB at 78%', callText.includes('78%')],
      ['CALL SELL table contains ADANIENT at 15%', callText.includes('15%')],
      ['PUT SELL table contains ADANIENT at 15%', putText.includes('15%')],
      ['CALL SELL table has no stray "—%" (blank IVP masquerading as a value)', !/—%/.test(callText)],
      ['Zero page errors', pageErrors.length === 0],
    ];

    for (const [desc, ok] of checks) {
      if (ok) console.log(`PASS: ${desc}`);
      else { fail(desc); failed = true; }
    }

    if (failed) {
      console.error('\nCALL SELL table text:', callText);
      console.error('PUT SELL table text:', putText);
      console.error('Page errors:', pageErrors);
    }
  } finally {
    if (browser) await browser.close();
    server.kill();
  }

  if (failed) process.exit(1);
  console.log('\nAll checks passed.');
}

run().catch(err => { console.error(err); process.exit(1); });
