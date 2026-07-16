const puppeteer = require('puppeteer');

// App Store Connect slot APP_IPHONE_65 -> 1242 x 2688 (414x896 CSS @3x)
const W = 414, H = 896, SCALE = 3;

(async () => {
  const browser = await puppeteer.launch({
    executablePath: '/usr/bin/chromium-browser',
    args: ['--no-sandbox', '--disable-gpu', '--force-color-profile=srgb'],
  });
  const page = await browser.newPage();
  await page.setViewport({ width: W, height: H, isMobile: true, hasTouch: true, deviceScaleFactor: SCALE });
  await page.goto('http://127.0.0.1:8091/', { waitUntil: 'networkidle2' });
  await new Promise(r => setTimeout(r, 1200));
  await page.type('#login-password', 'demo');
  await page.click('#login-form button[type="submit"]');
  await new Promise(r => setTimeout(r, 4000));

  // The demo banner is an artifact of this capture environment, not of the
  // shipped app — a real user never sees it. The UI and the sample data below
  // it are exactly what the product renders.
  await page.addStyleTag({ content: '#demo-banner, .demo-banner { display: none !important; }' });
  await new Promise(r => setTimeout(r, 400));

  const shot = async (name) => { await page.screenshot({ path: `/out/${name}.png` }); console.log('  ' + name + '.png'); };
  const tab = async (v, ms = 2800) => { await page.click(`.tab[data-view="${v}"]`); await new Promise(r => setTimeout(r, ms)); };

  await tab('server');   await shot('01-server');
  await tab('apps');     await shot('02-apps');
  await tab('health');   await shot('03-health');
  await tab('vibe');     await shot('04-vibe');
  await tab('terminal'); await shot('05-terminal');
  await browser.close();
})();
