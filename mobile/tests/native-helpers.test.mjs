import test from 'node:test';
import assert from 'node:assert/strict';
import { approvedApiBase, createGeolocationAdapter, externalHttpsUrl, safeFilename } from '../src/native-helpers.mjs';

test('mobile configuration rejects credentials, cleartext, query strings, and local-only APIs', () => {
  assert.equal(approvedApiBase('https://khadamati-app-api.onrender.com/'), 'https://khadamati-app-api.onrender.com');
  for (const url of ['http://example.com', 'https://u:p@example.com', 'https://example.com/?token=private', 'https://example.com/api', 'https://localhost', 'https://127.0.0.1', 'https://[::1]', 'javascript:alert(1)']) {
    assert.throws(() => approvedApiBase(url), url);
  }
});

test('external browser accepts HTTPS destinations and keeps local files out of it', () => {
  assert.equal(externalHttpsUrl('https://wa.me/96890000000', 'capacitor://localhost'), 'https://wa.me/96890000000');
  for (const url of ['javascript:alert(1)', 'file:///private/key', 'http://example.com', 'https://user:secret@example.com', 'https://localhost/profile', '/assets/file.pdf']) {
    assert.equal(externalHttpsUrl(url, 'https://localhost'), null);
  }
});

test('shared filenames cannot escape the app cache and preserve Arabic names', () => {
  assert.ok(!safeFilename('../../secret').startsWith('.'));
  assert.ok(!safeFilename('../../secret').includes('/'));
  assert.equal(safeFilename('اتفاقية-خدماتي.pdf'), 'اتفاقية-خدماتي.pdf');
  assert.ok(!/[\\/\n]/.test(safeFilename('..\\test\nfile')));
});

test('a cancelled pending location watch cannot deliver coordinates or remain active', async () => {
  let resolveId, callback;
  const cleared = [], positions = [];
  const adapter = createGeolocationAdapter({
    watchPosition(_options, listener) { callback = listener; return new Promise(resolve => { resolveId = resolve; }); },
    async clearWatch({ id }) { cleared.push(id); },
  });
  const id = adapter.watchPosition(position => positions.push(position));
  adapter.clearWatch(id);
  callback({ coords: { latitude: 23, longitude: 58 } });
  resolveId('native-watch');
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(positions, []);
  assert.deepEqual(cleared, ['native-watch']);
});

test('location permission failures remain failures and never create fake coordinates', async () => {
  const adapter = createGeolocationAdapter({
    async getCurrentPosition() { throw { code: 'OS_PLUG_GLOC_0003', message: 'denied' }; },
    async checkPermissions() { return { location: 'denied', coarseLocation: 'denied' }; },
  });
  const error = await new Promise(resolve => adapter.getCurrentPosition(() => assert.fail('Denied location succeeded'), resolve));
  assert.equal(error.code, 1);
  assert.equal(await adapter.permissionState(), 'denied');
});

test('Android approximate location permission is sufficient', async () => {
  const adapter = createGeolocationAdapter({ async checkPermissions() { return { location: 'prompt', coarseLocation: 'granted' }; } });
  assert.equal(await adapter.permissionState(), 'granted');
});
