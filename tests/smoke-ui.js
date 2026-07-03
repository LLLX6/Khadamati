const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const BASE_URL = process.env.KHADAMATI_TEST_URL || 'http://127.0.0.1:8080/';
const CHROME_PATH = process.env.CHROME_PATH || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const SCREENSHOT_DIR = process.env.KHADAMATI_SCREENSHOT_DIR || '';

function assert(value, message) {
  if (!value) throw new Error(message);
}

async function capture(page, name) {
  if (!SCREENSHOT_DIR) return;
  fs.mkdirSync(SCREENSHOT_DIR, { recursive: true });
  await page.screenshot({ path: path.join(SCREENSHOT_DIR, `${name}.png`), fullPage: true });
}

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: CHROME_PATH });
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 2,
    isMobile: true,
    hasTouch: true,
    locale: 'ar-OM',
  });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => {
    if (message.type() === 'error' && !/favicon|tile|Failed to load resource/.test(message.text())) {
      errors.push(message.text());
    }
  });

  // Keep the smoke test local and deterministic without writing test accounts to the server.
  await page.route('**/api/**', route => route.abort());
  await page.goto(BASE_URL, { waitUntil: 'domcontentloaded' });
  await page.evaluate(() => localStorage.clear());
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.waitForSelector('[data-action="openUserLogin"]');

  await page.locator('[data-action="openUserLogin"]').click();
  await page.locator('#customerLoginPhone').fill('95550001');
  await page.locator('#customerLoginName').fill('مستخدم الاختبار الآلي');
  await page.locator('[data-action="customerLogin"]').click();
  await page.waitForSelector('.role-onboarding');
  await page.locator('[data-action="skipOnboarding"]').click();

  assert((await page.locator('.clean-grid .category-tile').count()) <= 6, 'Home must show no more than six categories.');
  assert(await page.locator('.popular-rail').count(), 'Popular services rail is missing.');
  assert(await page.locator('.context-tip').count(), 'Contextual assistant tip is missing.');
  await capture(page, '01-user-home');

  await page.locator('.popular-rail [data-action="serviceSheet"]').first().click();
  await page.locator('[data-action="quickRequestForService"]').click();
  await page.waitForSelector('.request-wizard');
  await page.locator('#qrName').fill('مستخدم الاختبار الآلي');
  await page.locator('#qrPhone').fill('95550001');
  await page.locator('#qrService').selectOption({ index: 0 });
  await page.locator('[data-action="requestWizardNext"][data-step="2"]').click();
  await page.locator('#qrNote').fill('أحتاج تنفيذ هذه الخدمة في المنزل خلال هذا الأسبوع');
  await page.locator('[data-action="requestWizardNext"][data-step="3"]').click();
  assert(await page.locator('.match-summary').count(), 'Request matching summary is missing.');
  await page.locator('[data-action="saveQuickRequest"]').click();
  await page.waitForSelector('.active-request-home');
  await page.locator('.bottom-nav [data-action="nav"][data-view="myAccount"]').click();
  await page.locator('[data-action="openAppearance"]').click();
  await page.locator('[data-action="setDisplayScale"][data-value="large"]').click();
  assert(await page.locator('body').getAttribute('data-scale') === 'large', 'Large text mode was not applied.');
  await page.locator('[data-action="closeModal"]').click();
  await page.locator('.bottom-nav [data-action="nav"][data-view="home"]').click();

  await page.locator('[data-action="goBack"]').click();
  await page.locator('[data-action="enterProvider"]').click();
  await page.locator('#loginPhone').fill('91234567');
  await page.locator('#loginOtp').fill('1234');
  await page.locator('[data-action="providerLogin"]').click();
  await page.waitForSelector('.role-onboarding');
  await page.locator('[data-action="skipOnboarding"]').click();
  await page.waitForTimeout(200);
  if (await page.locator('#modalRoot .modal-backdrop.show').count()) {
    assert(await page.locator('#modalRoot .notification-disclosure').count(), 'Provider login notification popup is empty.');
    await page.locator('#modalRoot [data-action="closeModal"]').first().click();
  }
  assert(await page.locator('.week-calendar').count(), 'Provider weekly calendar is missing.');
  assert(await page.locator('.quote-template-grid').count(), 'Provider quote templates are missing.');
  await capture(page, '02-provider-dashboard');
  await page.locator('[data-action="openQuoteLibrary"]').first().click();
  assert(await page.locator('.modal-title').filter({ hasText: /عرض السعر|price and duration/i }).count(), 'Quote template sheet did not open.');
  await page.locator('[data-action="closeModal"]').click();

  await page.locator('[data-action="providerLogout"]').click();
  for (let i = 0; i < 6; i++) await page.locator('[data-action="brandHome"]').first().click();
  await page.waitForSelector('#adminCode');
  await page.locator('#adminCode').fill('0000');
  await page.locator('[data-action="adminLogin"]').click();
  await page.waitForSelector('.admin-shell');
  await page.locator('[data-action="adminTab"][data-tab="ads"]').click();
  await page.locator('#adImages').setInputFiles(path.join(__dirname, '..', 'app-icon-512.png'));
  await page.locator('[data-action="previewAdDraft"]').click();
  await page.waitForSelector('.ad-preview-device');
  assert(await page.locator('.ad-preview-device').count() === 2, 'Phone and desktop ad previews are missing.');
  await page.locator('[data-action="closeModal"]').click();
  await page.locator('[data-action="adminTab"][data-tab="quality"]').click();
  assert(await page.locator('.system-health').count(), 'System health monitoring panel is missing.');
  await capture(page, '03-admin-quality');

  const fits = await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);
  assert(fits, 'Mobile layout overflows horizontally.');
  assert(errors.length === 0, `Browser errors: ${errors.join(' | ')}`);

  console.log(JSON.stringify({
    ok: true,
    userFlow: true,
    requestFlow: true,
    providerFlow: true,
    adminFlow: true,
    mobileFit: fits,
  }, null, 2));
  await browser.close();
})().catch(async error => {
  console.error(error);
  process.exit(1);
});
