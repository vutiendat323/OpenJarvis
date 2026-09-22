// Explicit local acceptance runner; requires a running kiosk backend/frontend.
// PLAYWRIGHT_CORE_PATH=/path/to/playwright-core node scripts/verify-shared-browser.cjs --live-model
// Only the local fixture is edited. No orders or payments are submitted.
const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_CORE_PATH || 'playwright-core');

const backend = process.env.SHARED_BROWSER_API || 'http://127.0.0.1:8000';
const frontend = process.env.SHARED_BROWSER_UI || 'http://127.0.0.1:5173';
const liveModel = process.argv.includes('--live-model');
const fixture = http.createServer((request, response) => {
  response.writeHead(200, {
    'Content-Type': 'text/html; charset=utf-8',
    'X-Frame-Options': 'DENY',
    'Content-Security-Policy': "frame-ancestors 'none'; default-src 'self' 'unsafe-inline'",
  });
  response.end(`<!doctype html><title>Shared Browser Acceptance</title>
    <body style="margin:0;background:#faf0dc;font:20px sans-serif;min-height:1800px">
    <h1>Shared Browser Acceptance</h1>
    <input aria-label="Order note" style="position:absolute;left:20px;top:100px;width:250px;height:40px">
    <button style="position:absolute;left:20px;top:170px;width:200px;height:45px" onclick="this.textContent='Clicked '+(++window.count);document.body.style.background=window.count%2?'rgb(208,0,0)':'rgb(0,208,0)'">Click me</button>
    <input type="password" aria-label="Password" style="position:absolute;left:20px;top:240px;width:250px;height:40px">
    <a href="/next" target="_blank" style="position:absolute;left:20px;top:320px">Next page</a>
    <script>window.count=0;</script>`);
});

(async () => {
  await new Promise(resolve => fixture.listen(0, '127.0.0.1', resolve));
  const fixtureUrl = `http://127.0.0.1:${fixture.address().port}/`;
  const portFile = path.resolve('.openjarvis/ordering-kiosk/shared-browser-profile/DevToolsActivePort');
  const cdp = `http://127.0.0.1:${fs.readFileSync(portFile, 'utf8').split('\n')[0]}`;
  const remote = await chromium.connectOverCDP(cdp);
  const human = await chromium.launch({executablePath: process.env.CHROME_BIN || '/usr/bin/google-chrome', headless: true});
  try {
    const sharedPage = remote.contexts()[0].pages()[0];
    const initialTargets = await (await fetch(cdp + '/json/list')).json();
    const target = initialTargets.find(item => item.type === 'page').id;
    const ui = await human.newPage({viewport: {width: 1440, height: 1000}});
    const errors = [];
    ui.on('pageerror', error => errors.push(error.message));
    await ui.addInitScript(() => {
      localStorage.setItem('openjarvis-optin-seen', 'true');
      localStorage.setItem('openjarvis_kiosk_visualizer_settings', JSON.stringify({style: 'screen', showPet: false}));
      window.captureCalls = 0;
      navigator.mediaDevices.getDisplayMedia = () => { window.captureCalls++; throw new Error('Unexpected capture'); };
    });
    const ensured = ui.waitForResponse(response => response.url().endsWith('/api/kiosk/presentation/ensure') && response.ok());
    await ui.goto(frontend + '/kiosk');
    const address = ui.getByLabel('Browser address');
    await address.waitFor();
    // Wait for the existing presentation initialization before user navigation.
    await ensured;
    await address.fill(fixtureUrl);
    await address.press('Enter');
    await sharedPage.waitForURL(fixtureUrl, {waitUntil: 'domcontentloaded'});
    const viewport = ui.getByLabel('Shared browser viewport', {exact: true});
    await viewport.locator('img').waitFor();
    await viewport.click({position: {x: 100, y: 120}});
    await ui.keyboard.type('Human note');
    await ui.keyboard.insertText(' cà phê');
    await sharedPage.waitForFunction(() => document.querySelector('input')?.value === 'Human note cà phê');

    if (liveModel) {
      const started = performance.now();
      const result = await fetch(backend + '/v1/chat/completions', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        signal: AbortSignal.timeout(120000),
        body: JSON.stringify({model: process.env.SHARED_BROWSER_MODEL || 'gpt-5.6-luna', stream: false,
          messages: [{role: 'user', content: 'Bài kiểm tra co-browsing trên fixture local đang mở: dùng browser_snapshot để đọc trang hiện tại, rồi thay nội dung ô Order note thành AI shared verified bằng browser_type. Chỉ sửa ô này trên trang hiện tại; không điều hướng, không tạo đơn hàng hay thanh toán. Sau đó xác nhận tiêu đề trang và nội dung vừa nhập.'}]}),
      });
      assert.equal(result.status, 200, `Model request failed: ${result.status}`);
      const body = await result.json();
      await sharedPage.waitForFunction(() => document.querySelector('input')?.value === 'AI shared verified');
      console.log(JSON.stringify({live_model: true, elapsed_ms: Math.round(performance.now() - started), answer: body.choices?.[0]?.message?.content?.slice(0, 400)}));
    }

    const timings = [];
    for (let index = 0; index < 30; index++) {
      await ui.evaluate(expectedRed => {
        const viewport = document.querySelector('[aria-label="Shared browser viewport"]');
        const img = viewport.querySelector('img');
        const canvas = document.createElement('canvas'); canvas.width = 1; canvas.height = 1;
        const ctx = canvas.getContext('2d');
        window.frameLatency = null;
        let started = 0;
        viewport.addEventListener('pointerup', () => { started = performance.now(); }, {once: true});
        const loaded = () => {
          ctx.drawImage(img, 0, 0, 1, 1, 0, 0, 1, 1);
          const pixel = ctx.getImageData(0, 0, 1, 1).data;
          if (started && (expectedRed ? pixel[0] > 180 && pixel[1] < 30 : pixel[1] > 180 && pixel[0] < 30)) {
            img.removeEventListener('load', loaded);
            requestAnimationFrame(() => requestAnimationFrame(() => { window.frameLatency = performance.now() - started; }));
          }
        };
        img.addEventListener('load', loaded);
      }, index % 2 === 0);
      await viewport.click({position: {x: 100, y: 190}});
      await ui.waitForFunction(() => window.frameLatency !== null, {}, {timeout: 3000});
      timings.push(await ui.evaluate(() => window.frameLatency));
    }
    timings.sort((a, b) => a - b);
    console.log(JSON.stringify({metric: 'pointerup_to_changed_frame_paint_ms', samples: timings.length, p50: timings[14], p95: timings[28], max: timings[29]}));
    await viewport.click({position: {x: 80, y: 330}});
    await sharedPage.waitForURL(fixtureUrl + 'next');
    await ui.waitForFunction(() => document.querySelector('[aria-label="Browser address"]')?.value.endsWith('/next'));
    const targets = (await (await fetch(cdp + '/json/list')).json()).filter(item => item.type === 'page');
    assert.equal(targets.length, 1);
    assert.equal(targets[0].id, target);
    await ui.getByLabel('Back', {exact: true}).click();
    await sharedPage.waitForFunction(expected => location.href === expected && document.readyState === 'complete', fixtureUrl);
    await ui.getByLabel('Collapse voice pane').click();
    await ui.getByLabel('Expand voice pane').waitFor();
    assert.equal(await ui.evaluate(() => window.captureCalls), 0);
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({passed: true, same_target: target, human_input: true, restrictive_csp: true, no_screen_capture: true, live_model: liveModel}));
    // Restore the kiosk display before closing the disposable fixture.
    await address.fill(frontend + '/customer-display');
    const session = await (await fetch(backend + '/api/kiosk/presentation/ensure', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({display_origin: frontend})})).json();
    if (session.presentation_session_id) await sharedPage.goto(`${frontend}/customer-display?session=${session.presentation_session_id}`);
  } finally {
    await human.close();
    await remote.close();
    fixture.closeAllConnections();
    await new Promise(resolve => fixture.close(resolve));
  }
})().catch(error => { console.error(error); fixture.closeAllConnections(); fixture.close(); process.exitCode = 1; });
