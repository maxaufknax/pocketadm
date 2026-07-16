const puppeteer = require('puppeteer');

// App Store Connect slot APP_IPHONE_65 -> 1242 x 2688 (414x896 CSS @3x).
// Since v0.16 that is the only required slot (the app ships iPhone-only).
const W = 414, H = 896, SCALE = 3;
const BASE = process.env.BASE || 'http://127.0.0.1:8091/';

const wait = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const browser = await puppeteer.launch({
    executablePath: '/usr/bin/chromium-browser',
    args: ['--no-sandbox', '--disable-gpu', '--force-color-profile=srgb'],
  });
  const page = await browser.newPage();
  await page.setViewport({ width: W, height: H, isMobile: true, hasTouch: true, deviceScaleFactor: SCALE });
  const errors = [];
  page.on('pageerror', (e) => errors.push('PAGEERROR: ' + e.message));
  await page.goto(BASE, { waitUntil: 'networkidle2' });
  await wait(1200);
  await page.type('#login-password', 'demo');
  await page.click('#login-form button[type="submit"]');
  await wait(4000);

  // The demo banner is an artifact of this capture environment, not of the
  // shipped app — a real user never sees it. The UI and the sample data below
  // it are exactly what the product renders.
  await page.addStyleTag({ content: '#demo-banner, .demo-banner { display: none !important; }' });
  await wait(400);

  const shot = async (name) => { await page.screenshot({ path: `/out/${name}.png` }); console.log('  ' + name + '.png'); };
  const tab = async (v, ms = 2800) => {
    const found = await page.evaluate((v) => {
      const t = document.querySelector(`.tab[data-view="${v}"]`);
      if (!t) return false; t.click(); return true;
    }, v);
    if (!found) throw new Error(`no tab "${v}" — the tabbar changed, update this script`);
    await wait(ms);
  };

  // Order is the order the store shows them: what the app is, then the thing it
  // is actually for, then the rest.
  await tab('home');     await shot('01-home');
  await tab('vibe');     await shot('02-vibe');
  await tab('apps');     await shot('03-apps');
  await tab('health');   await shot('04-health');

  // The Terminal tab opens on the launcher, not on a shell — screenshotting it
  // straight away yields an empty black rectangle. Tap the first target to get
  // the simulated demo shell that the tab is meant to show off.
  await tab('terminal', 1500);
  const started = await page.evaluate(() => {
    const row = document.querySelector('#term-start .ts-row');
    if (!row) return false; row.click(); return true;
  });
  if (!started) throw new Error('terminal launcher had no .ts-row to open');
  await wait(2500);
  // A bare prompt is a poor advert for the terminal. Run something so the shot
  // shows output — and so an empty capture can't pass unnoticed.
  await page.click('#terminal');
  await page.keyboard.type('docker ps', { delay: 55 });
  await page.keyboard.press('Enter');
  await wait(1500);
  const termText = await page.evaluate(() =>
    document.querySelector('.xterm-rows')?.innerText.trim() || '');
  if (!termText.includes('docker ps')) throw new Error('terminal took no input — not shipping that as a screenshot');
  // Typing focused the shell, which raises body.kb-open and slides the tabbar
  // away for the keyboard. Correct on a device, but it would leave this the one
  // store screenshot without navigation — so drop focus and let it come back.
  await page.evaluate(() => document.activeElement?.blur());
  await wait(900);
  const tabbarBack = await page.evaluate(() => !document.body.classList.contains('kb-open'));
  if (!tabbarBack) throw new Error('tabbar still hidden — kb-open stuck after blur');
  await shot('05-terminal');

  if (errors.length) { console.error('JS ERRORS:\n' + errors.join('\n')); process.exitCode = 1; }
  await browser.close();
})();
