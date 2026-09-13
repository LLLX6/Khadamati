const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// Exercise the shipped handlers with controlled HTTP responses. Fixtures are
// deliberately synthetic; these tests never connect to a deployed service.
const source = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const authSource = source.slice(source.indexOf('function openAdminLogin('), source.indexOf('function destroyLeafletMaps('));

function harness(responses) {
  const requests = [], errors = [], invalid = [];
  let fields = {}, modal = '', activations = 0;
  const context = vm.createContext({
    DEVICE_ID: 'fixture-device', ADMIN_LOGIN_FIRST_FACTOR: null, ADMIN_RECOVERY_CODES: [],
    AUTH: {}, S: {}, L: (_ar, en) => en, tr: key => key, esc: String, icon: () => '',
    val: id => fields[id] || '',
    openModal: html => { modal = html; fields = {}; },
    closeModal: () => { modal = ''; fields = {}; context.ADMIN_LOGIN_FIRST_FACTOR = null; },
    markInvalid: (id, message) => invalid.push({ id, message }),
    showError: error => errors.push(error.message), toast: message => errors.push(message),
    niceError: error => error.message, save() {}, render() {}, mergeRemote() {},
    applySessionResponse: (_kind, response) => { activations++; context.AUTH.adminToken = response.token; },
    api: async (url, body, token) => {
      requests.push({ url, body: body && JSON.parse(JSON.stringify(body)), token });
      const response = responses.shift();
      assert.ok(response, `Unexpected request: ${url}`);
      if (response instanceof Error) throw response;
      return response;
    },
  });
  vm.runInContext(authSource, context);
  return {
    context, requests, errors, invalid,
    setFields: values => { fields = values; },
    action: action => context.handleAdminAuthenticationAction(action),
    get modal() { return modal; },
    get activations() { return activations; },
  };
}

for (const method of ['email', 'password']) {
  test(`${method}: complete second factor before opening the admin session`, async () => {
    const h = harness([{ twoFactorRequired: true }, new Error('admin_2fa_invalid'), { token: 'fixture-session' }, {}]);
    const firstFactor = method === 'email'
      ? { emailChallengeId: 'fixture-email-challenge', emailCode: '592814' }
      : { code: 'Fixture-Admin-Only' };
    h.setFields(method === 'email'
      ? { adminEmailChallengeId: firstFactor.emailChallengeId, adminEmailCode: firstFactor.emailCode }
      : { adminCode: firstFactor.code });
    await h.action(method === 'email' ? 'adminEmailLogin' : 'adminLogin');
    assert.match(h.modal, /id="adminTwoFactorCode"/);
    assert.equal(h.activations, 0);
    assert.equal(h.requests.length, 1);
    h.setFields({ adminTwoFactorCode: '111111' });
    await h.action('adminLogin');
    assert.deepEqual(h.errors, ['admin_2fa_invalid']);
    assert.equal(h.activations, 0);
    h.setFields({ adminTwoFactorCode: '222222' });
    await h.action('adminLogin');
    assert.deepEqual(h.requests[2].body, { ...firstFactor, twoFactorCode: '222222', deviceId: 'fixture-device' });
    assert.equal(h.requests[3].url, '/api/admin/session');
    assert.equal(h.requests[3].token, 'fixture-session');
    assert.equal(h.activations, 1);
    assert.ok(!h.context.ADMIN_LOGIN_FIRST_FACTOR);
    assert.equal(h.modal, '');
  });

  test(`${method}: first login must finish authenticator enrollment`, async () => {
    const h = harness([
      { twoFactorSetupRequired: true, challengeId: 'fixture-setup', secret: 'FIXTURE-ONLY' },
      { token: 'fixture-session', recoveryCodes: ['fixture-recovery'] }, {},
    ]);
    h.setFields({ adminCode: 'Fixture-Admin-Only', adminEmailChallengeId: 'fixture-email-challenge', adminEmailCode: '592814' });
    await h.action(method === 'email' ? 'adminEmailLogin' : 'adminLogin');
    assert.match(h.modal, /confirmAdminTwoFactorSetup/);
    assert.equal(h.activations, 0);
    assert.ok(!h.context.ADMIN_LOGIN_FIRST_FACTOR);
    h.setFields({ adminTwoFactorChallenge: 'fixture-setup', adminTwoFactorCode: '222222' });
    await h.action('confirmAdminTwoFactorSetup');
    assert.equal(h.requests[1].url, '/api/admin/2fa/setup');
    assert.equal(h.activations, 1);
    assert.match(h.modal, /finishAdminTwoFactorSetup/);
  });
}

test('restarting sign-in discards the pending first factor', async () => {
  const h = harness([{ twoFactorRequired: true }]);
  h.setFields({ adminEmailChallengeId: 'fixture-email-challenge', adminEmailCode: '592814' });
  await h.action('adminEmailLogin');
  await h.action('restartAdminLogin');
  h.setFields({ adminTwoFactorCode: '222222' });
  await h.action('adminLogin');
  assert.ok(!h.context.ADMIN_LOGIN_FIRST_FACTOR);
  assert.equal(h.requests.length, 1);
  assert.equal(h.invalid[0].id, 'adminCode');
});

test('an incomplete login response cannot reuse a previous admin token', async () => {
  const h = harness([{}]);
  h.context.AUTH.adminToken = 'old-fixture-session';
  h.setFields({ adminCode: 'Fixture-Admin-Only' });
  await h.action('adminLogin');
  assert.equal(h.activations, 0);
  assert.equal(h.requests.length, 1);
  assert.deepEqual(h.errors, ['admin_2fa_required']);
});

test('password login still works when the server does not require a second factor', async () => {
  const h = harness([{ token: 'fixture-session' }, {}]);
  h.setFields({ adminCode: 'Fixture-Admin-Only' });
  await h.action('adminLogin');
  assert.equal(h.activations, 1);
  assert.equal(h.requests[1].url, '/api/admin/session');
});
