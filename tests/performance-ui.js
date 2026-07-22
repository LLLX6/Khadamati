const { chromium } = require('playwright');
const fs = require('fs');
const http = require('http');
const path = require('path');

const BASE_URL = process.env.KHADAMATI_TEST_URL || '';
const CHROME_PATH = process.env.CHROME_PATH || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
let localServer;

async function startStaticServer() {
  const root = path.resolve(__dirname, '..');
  const mime = {
    '.html': 'text/html; charset=utf-8',
    '.js': 'text/javascript; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.json': 'application/json; charset=utf-8',
    '.svg': 'image/svg+xml',
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.webp': 'image/webp',
    '.ico': 'image/x-icon',
  };
  localServer = http.createServer(async (request, response) => {
    try {
      const pathname = decodeURIComponent(new URL(request.url, 'http://127.0.0.1').pathname);
      const relative = pathname === '/' ? 'index.html' : pathname.replace(/^\/+/, '');
      const target = path.resolve(root, relative);
      if (target !== root && !target.startsWith(`${root}${path.sep}`)) {
        response.writeHead(403).end();
        return;
      }
      const data = await fs.promises.readFile(target);
      response.writeHead(200, {
        'content-type': mime[path.extname(target).toLowerCase()] || 'application/octet-stream',
        'cache-control': 'no-store',
      });
      response.end(data);
    } catch {
      response.writeHead(404).end();
    }
  });
  await new Promise((resolve, reject) => {
    localServer.once('error', reject);
    localServer.listen(0, '127.0.0.1', resolve);
  });
  return `http://127.0.0.1:${localServer.address().port}/`;
}

(async () => {
  const testUrl = BASE_URL || await startStaticServer();
  const browser = await chromium.launch({ headless: true, executablePath: CHROME_PATH });
  const page = await browser.newPage({
    viewport: { width: 390, height: 844 },
    isMobile: true,
    hasTouch: true,
    locale: 'ar-OM',
  });
  const startedAt = Date.now();
  await page.goto(testUrl, { waitUntil: 'load' });
  await page.waitForSelector('[data-action="openUserLogin"]');
  const metrics = await page.evaluate(() => {
    const navigation = performance.getEntriesByType('navigation')[0];
    const paint = performance.getEntriesByName('first-contentful-paint')[0];
    return {
      domContentLoadedMs: Math.round(navigation.domContentLoadedEventEnd),
      loadMs: Math.round(navigation.loadEventEnd),
      firstContentfulPaintMs: paint ? Math.round(paint.startTime) : null,
      transferredBytes: navigation.transferSize,
    };
  });
  metrics.interactiveReadyMs = Date.now() - startedAt;
  metrics.withinLocalTarget = metrics.interactiveReadyMs < 3500;
  console.log(JSON.stringify(metrics, null, 2));
  await browser.close();
  localServer?.close();
  if (!metrics.withinLocalTarget) process.exit(1);
})().catch(error => {
  console.error(error);
  localServer?.close();
  process.exit(1);
});
