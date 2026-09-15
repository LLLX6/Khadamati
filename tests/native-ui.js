/* Exercises the packaged UI and real bridge with mocked device APIs and an
   isolated HTTP API. Device permission dialogs and store signing need devices. */
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const http = require('node:http');
const path = require('node:path');
const { chromium } = require('playwright');
const { build } = require('../mobile/node_modules/esbuild');

async function main() {
  const root = path.resolve(__dirname, '../mobile/www');
  const mime = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.png': 'image/png', '.webp': 'image/webp', '.svg': 'image/svg+xml' };
  const server = http.createServer(async (request, response) => {
    try {
      const name = decodeURIComponent(new URL(request.url, 'http://localhost').pathname);
      const target = path.resolve(root, name === '/' ? 'index.html' : `.${name}`);
      if (!target.startsWith(`${root}${path.sep}`)) return response.writeHead(403).end();
      const bytes = await fs.readFile(target);
      response.writeHead(200, { 'content-type': mime[path.extname(target)] || 'application/octet-stream' });
      response.end(bytes);
    } catch { response.writeHead(404).end(); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  let browser;
  try {
    const compiled = await build({
      entryPoints: [path.resolve(__dirname, '../mobile/src/native-bridge.js')], bundle: true,
      write: false, format: 'iife', define: { __KHADAMATI_MOBILE_API__: JSON.stringify('https://mobile-api.invalid') },
      plugins: [{ name: 'device-api-fixtures', setup(builder) {
        builder.onResolve({ filter: /^@capacitor\// }, args => ({ path: args.path, namespace: 'device' }));
        builder.onLoad({ filter: /.*/, namespace: 'device' }, args => {
          const name = { core: 'Capacitor', app: 'App', browser: 'Browser', filesystem: 'Filesystem', geolocation: 'Geolocation', share: 'Share' }[args.path.split('/')[1]];
          return { contents: `export const ${name} = globalThis.__device.${name}; export const Directory = { Cache: 'CACHE' };` };
        });
      } }],
    });
    browser = await chromium.launch({ headless: true, ...(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {}) });
    for (const platform of ['ios', 'android']) {
      const context = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
      await context.addInitScript(platform => {
        window.__nativeCalls = { files: [], shares: [], links: [], watches: [], minimized: 0, registrations: 0 };
        window.__nativeListeners = {};
        const calls = window.__nativeCalls;
        if (navigator.serviceWorker) navigator.serviceWorker.register = async () => { calls.registrations++; throw new Error('Unexpected native service worker'); };
        window.__device = {
          Capacitor: { isNativePlatform: () => true, getPlatform: () => platform },
          App: { async addListener(name, callback) { window.__nativeListeners[name] = callback; return { remove() {} }; }, async minimizeApp() { calls.minimized++; } },
          Browser: { async open(options) { calls.links.push(options); } },
          Filesystem: { async readdir() { return { files: [] }; }, async rmdir() {}, async writeFile(options) { calls.files.push(options); return { uri: `file:///cache/${options.path}` }; } },
          Share: { async share(options) { calls.shares.push(options); } },
          Geolocation: {
            async checkPermissions() { return { location: 'granted', coarseLocation: 'granted' }; },
            async getCurrentPosition() { return { coords: { latitude: 23.61, longitude: 58.24, accuracy: 20 } }; },
            async watchPosition() { calls.watches.push('started'); return 'watch-1'; },
            async clearWatch() { calls.watches.push('cleared'); },
          },
        };
      }, platform);
      const requests = [], errors = [];
      await context.route('**/*', async route => {
        const url = new URL(route.request().url());
        if (url.pathname === '/native-bridge.js') return route.fulfill({ contentType: 'text/javascript', body: compiled.outputFiles[0].text });
        if (url.origin === 'https://mobile-api.invalid') {
          requests.push(url.pathname);
          const headers = { 'access-control-allow-origin': origin, 'access-control-allow-credentials': 'true', 'access-control-allow-headers': 'authorization,content-type', 'access-control-allow-methods': 'GET,POST,OPTIONS' };
          const user = { id: 'native-test-user', name: 'مستخدم الاختبار', phone: '96895550001', gov: 'مسقط', wilayah: 'السيب', pinConfigured: true };
          let data = { ok: true };
          if (url.pathname === '/api/bootstrap') data = { platform: { subscriptionsEnabled: false, paymentGatewayEnabled: false }, providers: [] };
          if (url.pathname === '/api/users/login') data = { token: 'native-test-token', user };
          if (['/api/auth/persist', '/api/auth/refresh'].includes(url.pathname)) data = { token: 'native-test-token', sessionKind: 'user', session: { kind: 'user', userId: user.id, name: user.name } };
          if (url.pathname === '/api/user/session') data = { user, requests: [], notifications: [] };
          return route.fulfill({ status: 200, contentType: 'application/json', headers, body: JSON.stringify(data) });
        }
        if (url.origin === origin) return route.continue();
        return route.abort();
      });
      const page = await context.newPage();
      page.on('pageerror', error => errors.push(error.message));
      await page.goto(origin, { waitUntil: 'domcontentloaded' });
      await page.locator('[data-action="openUserLogin"]').first().waitFor();
      if (await page.locator('[data-action="skipOnboarding"]').isVisible()) await page.locator('[data-action="skipOnboarding"]').click();
      await page.locator('[data-action="openUserLogin"]').first().click();
      await page.locator('#customerLoginPhone').waitFor();
      if (platform === 'android') {
        await page.evaluate(() => window.__nativeListeners.backButton());
        await page.locator('#customerLoginPhone').waitFor({ state: 'hidden' });
        assert.equal(await page.evaluate(() => window.__nativeCalls.minimized), 0, 'Back minimized an open login sheet');
        await page.locator('[data-action="openUserLogin"]').first().click();
      }
      await page.locator('#customerLoginPhone').fill('95550001');
      await page.locator('#customerLoginPin').fill('2468');
      await page.locator('[data-action="customerLogin"]').click();
      await page.locator('.bottom-nav').waitFor();
      if (await page.locator('[data-action="skipOnboarding"]').isVisible()) await page.locator('[data-action="skipOnboarding"]').click();
      assert.ok(requests.includes('/api/users/login'), 'Native login never reached the configured API');
      assert.ok(requests.includes('/api/bootstrap'), 'Native startup never loaded the configured API');
      await page.evaluate(async () => {
        await window.KhadamatiNative.download('اتفاقية.ics', 'BEGIN:VCALENDAR\nEND:VCALENDAR', 'text/calendar');
        window.open('https://wa.me/96890000000', '_blank');
      });
      await page.waitForFunction(() => window.__nativeCalls.links.length === 1);
      const native = await page.evaluate(() => window.__nativeCalls);
      assert.equal(native.registrations, 0, 'Native app registered the PWA update worker');
      assert.equal(native.shares.length, 1);
      assert.ok(native.shares[0].files[0].startsWith('file:///cache/khadamati-share/'));
      assert.equal(Buffer.from(native.files[0].data, 'base64').toString(), 'BEGIN:VCALENDAR\nEND:VCALENDAR');
      assert.equal(native.links[0].url, 'https://wa.me/96890000000');
      for (const language of ['en', 'ar', 'hi', 'bn', 'ur']) {
        await page.locator('[data-action="toggleLang"]:visible').first().click();
        await page.locator(`[data-language-picker] [data-action="setLanguage"][data-lang="${language}"]`).click();
        await page.waitForFunction(language => document.documentElement.lang === language, language);
        assert.equal(await page.evaluate(() => document.documentElement.dir), ['ar', 'ur'].includes(language) ? 'rtl' : 'ltr');
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
      }
      assert.deepEqual(errors, []);
      console.log(`${platform}: native API login, shared calendar, external links, languages, back handling and worker isolation passed.`);
      await context.close();
    }
  } finally {
    await browser?.close();
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
