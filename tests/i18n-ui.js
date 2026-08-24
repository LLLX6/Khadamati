const { chromium } = require('playwright');
const fs = require('fs');
const http = require('http');
const path = require('path');

const root = path.resolve(__dirname, '..');
const chromePath = process.env.CHROME_PATH || (fs.existsSync('C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe') ? 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe' : '');

function assert(value, message) {
  if (!value) throw new Error(message);
}

async function server() {
  const mime = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.webmanifest': 'application/manifest+json', '.json': 'application/json', '.svg': 'image/svg+xml', '.png': 'image/png', '.webp': 'image/webp' };
  const instance = http.createServer(async (request, response) => {
    try {
      const pathname = decodeURIComponent(new URL(request.url, 'http://127.0.0.1').pathname);
      const target = path.resolve(root, pathname === '/' ? 'index.html' : pathname.replace(/^\/+/, ''));
      if (target !== root && !target.startsWith(`${root}${path.sep}`)) return response.writeHead(403).end();
      const data = await fs.promises.readFile(target);
      response.writeHead(200, { 'content-type': mime[path.extname(target)] || 'application/octet-stream', 'cache-control': 'no-store' });
      response.end(data);
    } catch (_) {
      response.writeHead(404).end('Not found');
    }
  });
  await new Promise((resolve, reject) => {
    instance.once('error', reject);
    instance.listen(0, '127.0.0.1', resolve);
  });
  return { instance, url: `http://127.0.0.1:${instance.address().port}/` };
}

async function choose(page, language) {
  await page.locator('[data-action="toggleLang"]:visible').first().click();
  const picker = page.locator('[data-language-picker]');
  await picker.waitFor({ state: 'visible' });
  const codes = await picker.locator('[data-action="setLanguage"]').evaluateAll(items => items.map(item => item.dataset.lang));
  assert(JSON.stringify(codes) === JSON.stringify(['ar', 'en', 'hi', 'bn', 'ur']), `Unexpected language choices: ${codes.join(',')}`);
  await picker.locator(`[data-lang="${language}"]`).click();
  await page.waitForFunction(selected => document.documentElement.lang === selected, language);
}

async function assertPhoneWidths(page, label) {
  for (const width of [320, 390, 430]) {
    await page.setViewportSize({ width, height: 844 });
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), `${label} overflows at ${width}px.`);
  }
  await page.setViewportSize({ width: 390, height: 844 });
}

(async () => {
  const { instance, url } = await server();
  const browser = await chromium.launch({ headless: true, ...(chromePath ? { executablePath: chromePath } : {}) });
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, serviceWorkers: 'block', locale: 'ar-OM' });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  try {
    await page.goto(url, { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('[data-action="toggleLang"]');
    await page.waitForTimeout(150);
    const onboardingSkip = page.locator('[data-action="skipOnboarding"]');
    if (await onboardingSkip.count()) await onboardingSkip.last().click();
    const expectations = {
      ar: { dir: 'rtl', text: 'اختر مساحتك' },
      en: { dir: 'ltr', text: 'Choose your space' },
      hi: { dir: 'ltr', text: 'अपनी जगह चुनें' },
      bn: { dir: 'ltr', text: 'আপনার স্থান নির্বাচন করুন' },
      ur: { dir: 'rtl', text: 'اپنی جگہ منتخب کریں' },
    };
    for (const [language, expected] of Object.entries(expectations)) {
      await choose(page, language);
      assert(await page.locator('html').getAttribute('dir') === expected.dir, `${language} has the wrong direction.`);
      const heading = (await page.locator('.access-choice-head b').textContent()).trim();
      assert(heading === expected.text, `${language} entry copy is not translated: ${heading}`);
      if (language === 'hi' || language === 'bn') {
        const entryText = await page.locator('.access-gateway').innerText();
        assert(!/[\u0600-\u06ff]/u.test(entryText), `${language} entry still contains Arabic interface copy.`);
      }
      await assertPhoneWidths(page, `${language} entry`);
    }

    await page.locator('[data-action="enterProvider"]').click();
    const providerHeadings = {
      hi: 'एक ही जगह पर आपका काम',
      bn: 'একই স্থানে আপনার কাজ',
      ur: 'آپ کا کام ایک جگہ پر',
    };
    for (const [language, heading] of Object.entries(providerHeadings)) {
      await choose(page, language);
      assert((await page.locator('.business-brand h1').textContent()).trim() === heading, `${language} provider gateway is not translated.`);
      if (language === 'hi' || language === 'bn') {
        const gatewayText = await page.locator('.business-gateway').innerText();
        assert(!/[\u0600-\u06ff]/u.test(gatewayText), `${language} provider gateway still contains Arabic interface copy.`);
      }
      await assertPhoneWidths(page, `${language} provider gateway`);
    }
    await page.locator('[data-action="returnToEntry"]').click();
    await choose(page, 'ur');

    await page.reload({ waitUntil: 'domcontentloaded' });
    assert(await page.locator('html').getAttribute('lang') === 'ur', 'The selected language did not persist after reload.');
    assert(await page.locator('html').getAttribute('dir') === 'rtl', 'Persisted Urdu did not restore RTL.');

    await page.locator('[data-action="enterGuest"]').click();
    await page.waitForSelector('.app-top');
    const customerOnboardingSkip = page.locator('[data-action="skipOnboarding"]');
    if (await customerOnboardingSkip.count()) await customerOnboardingSkip.last().click();
    const customerHeadings = {
      ar: 'تصفح الأقسام',
      en: 'Browse categories',
      hi: 'श्रेणियाँ ब्राउज़ करें।',
      bn: 'বিভাগগুলো ব্রাউজ করুন',
      ur: 'زمرہ جات براؤز کریں',
    };
    for (const [language, heading] of Object.entries(customerHeadings)) {
      await choose(page, language);
      assert((await page.locator('.home-section-head h2').textContent()).trim() === heading, `${language} customer home is not translated.`);
      await assertPhoneWidths(page, `${language} customer home`);
    }
    await choose(page, 'ar');
    assert(errors.length === 0, `Browser errors: ${errors.join(' | ')}`);
    console.log(JSON.stringify({ ok: true, languages: Object.keys(expectations), persisted: 'ur', mobileWidth: 390 }));
  } finally {
    await browser.close();
    await new Promise(resolve => instance.close(resolve));
  }
})().catch(error => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
