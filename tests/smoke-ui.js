const { chromium } = require('playwright');
const fs = require('fs');
const http = require('http');
const path = require('path');

const BASE_URL = process.env.KHADAMATI_TEST_URL || '';
const DEFAULT_CHROME_PATH = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const CHROME_PATH = process.env.CHROME_PATH || (fs.existsSync(DEFAULT_CHROME_PATH) ? DEFAULT_CHROME_PATH : '');
const SCREENSHOT_DIR = process.env.KHADAMATI_SCREENSHOT_DIR || '';
const VIEWPORT_WIDTH = Number(process.env.KHADAMATI_VIEWPORT_WIDTH || 390);
const VIEWPORT_HEIGHT = Number(process.env.KHADAMATI_VIEWPORT_HEIGHT || 844);
const IS_MOBILE = VIEWPORT_WIDTH <= 760;
let LOCAL_SERVER = null;

const APP_SOURCE = fs.readFileSync(path.resolve(__dirname, '..', 'index.html'), 'utf8');
assertSource(APP_SOURCE.includes("const APP_VERSION = '1.1.0'") && APP_SOURCE.includes("const APP_BUILD = 'khadamati-v1.1.0-booking-v2-r1-2026-08-08'"), 'Booking v2 application version/build marker is missing.');
assertSource(APP_SOURCE.includes('actionPromptRoot') && APP_SOURCE.includes('renderActionPrompt()'), 'The actionable notification root is not wired to rendering.');
assertSource(APP_SOURCE.includes("'change_propose'") && APP_SOURCE.includes("'change_decide'"), 'Change-order propose/decision UI is missing.');
assertSource(APP_SOURCE.includes('work-order-summary') && APP_SOURCE.includes('review_change_order'), 'Work-order summary or change-order routing is missing.');
assertSource(APP_SOURCE.includes('bookingV2FeatureEnabled()') && APP_SOURCE.includes("action:'book'"), 'Instant booking is not gated by the server feature flag.');
assertSource(['booking_started', 'offer_accepted', 'work_started', 'completion_submitted', 'completion_resolved', 'completion_issue_opened', 'rating_submitted', 'rebook_started', 'action_prompt_completed'].every(name => APP_SOURCE.includes(`trackEvent('${name}'`)), 'Canonical booking lifecycle analytics are incomplete.');
assertSource(APP_SOURCE.includes("active:slot.active!==false&&Number(slot.active)!==0"), 'Cancelled provider instant slots are not normalized safely.');
assertSource(APP_SOURCE.includes("AUTH.activeRole!=='provider'") && APP_SOURCE.includes("AUTH.activeRole!=='user'"), 'Deep links do not enforce switching to their target account role.');
assertSource(APP_SOURCE.includes('!notification._offlineReadOnly&&!notificationStillActionable') && APP_SOURCE.includes('offlineMutationActions'), 'Offline notification details can still supersede or mutate server state.');
assertSource(APP_SOURCE.includes('foregroundInteractionInProgress()') && APP_SOURCE.includes('preserveForeground') && APP_SOURCE.includes('rememberPendingNotification(notification)'), 'Background sync or deep links can still replace an active form.');
assertSource(APP_SOURCE.includes('KHADAMATI_WORKFLOW_RETRY_INTENTS_V1') && APP_SOURCE.includes('clearWorkflowRetryIntent'), 'Completion retry idempotency is not persisted across network retries.');
assertSource(APP_SOURCE.includes("notification.requiresAction&&notification._serverVerified===true"), 'Local or inferred notifications can still interrupt the user.');
const INSTANT_CONFLICT_CODES = ['instant_slot_reserved', 'instant_slot_not_found', 'instant_slot_expired', 'instant_slot_policy_changed', 'provider_no_longer_available', 'instant_booking_stage_not_allowed', 'provider_area_mismatch', 'provider_daily_capacity_reached', 'instant_booking_conflict', 'instant_slot_service_mismatch'];
assertSource(INSTANT_CONFLICT_CODES.every(code => APP_SOURCE.includes(`'${code}'`)) && APP_SOURCE.includes('if(definitiveConflict&&intent.requestId)') && APP_SOURCE.includes('if(definitiveConflict)setTimeout(()=>instantBookingSheet'), 'Instant-booking conflicts are not wired to orphan cleanup and slot reselection.');
assertSource(['suggestion', 'community', 'offers', 'completion', 'change-order', 'quality', 'account'].every(route => APP_SOURCE.includes(`kind==='${route}'`)), 'Structured notification routes are incomplete.');

function assertSource(value, message) {
  if (!value) throw new Error(message);
}

async function startStaticServer() {
  const root = path.resolve(__dirname, '..');
  const mime = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.json': 'application/json; charset=utf-8', '.svg': 'image/svg+xml', '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp', '.ico': 'image/x-icon' };
  LOCAL_SERVER = http.createServer(async (request, response) => {
    try {
      const pathname = decodeURIComponent(new URL(request.url, 'http://127.0.0.1').pathname);
      const relative = pathname === '/' ? 'index.html' : pathname.replace(/^\/+/, '');
      const target = path.resolve(root, relative);
      if (target !== root && !target.startsWith(`${root}${path.sep}`)) {
        response.writeHead(403).end();
        return;
      }
      const data = await fs.promises.readFile(target);
      response.writeHead(200, { 'content-type': mime[path.extname(target).toLowerCase()] || 'application/octet-stream', 'cache-control': 'no-store' });
      response.end(data);
    } catch (_) {
      response.writeHead(404, { 'content-type': 'text/plain; charset=utf-8' });
      response.end('Not found');
    }
  });
  await new Promise((resolve, reject) => {
    LOCAL_SERVER.once('error', reject);
    LOCAL_SERVER.listen(0, '127.0.0.1', resolve);
  });
  return `http://127.0.0.1:${LOCAL_SERVER.address().port}/`;
}

function assert(value, message) {
  if (!value) throw new Error(message);
}

async function capture(page, name, options = {}) {
  if (!SCREENSHOT_DIR) return;
  fs.mkdirSync(SCREENSHOT_DIR, { recursive: true });
  await page.waitForTimeout(350);
  await page.screenshot({
    path: path.join(SCREENSHOT_DIR, `${name}.png`),
    fullPage: options.fullPage !== false,
  });
}

async function dismissProviderSectionGuide(page, captureName = '') {
  await page.waitForTimeout(260);
  const action = page.locator('[data-action="dismissProviderSectionGuide"]');
  if (!(await action.count())) return false;
  if (captureName) await capture(page, captureName, { fullPage: false });
  await action.last().click();
  return true;
}

async function clickUserNav(page, view) {
  if (view === 'conversations') {
    const conversations = page.locator('[data-action="openConversations"]:visible').first();
    assert(await conversations.count(), 'The conversations entry point is missing from the header.');
    await conversations.click();
    return;
  }
  if (view === 'search') {
    await clickUserNav(page, 'services');
    const search = page.locator('[data-action="nav"][data-view="search"]:visible, .services-search-action:visible').first();
    assert(await search.count(), 'The services page does not expose its search action.');
    await search.click();
    return;
  }
  const bottomItem = page.locator(`.bottom-nav [data-action="nav"][data-view="${view}"]`).first();
  await bottomItem.waitFor({ state: 'attached', timeout: 8000 }).catch(() => {});
  if (await bottomItem.count()) {
    if (await bottomItem.isVisible()) await bottomItem.click();
    else await bottomItem.evaluate(element => element.click());
    return;
  }
  const pageItem = page.locator(`[data-action="nav"][data-view="${view}"]`).first();
  await pageItem.waitFor({ state: 'attached', timeout: 3000 }).catch(() => {});
  assert(await pageItem.count(), `No accessible navigation route exists for ${view}.`);
  if (await pageItem.isVisible()) await pageItem.click();
  else await pageItem.evaluate(element => element.click());
}

async function clickFirstAction(page, action) {
  const item = page.locator(`[data-action="${action}"]`).first();
  assert(await item.count(), `No accessible action exists for ${action}.`);
  if (await item.isVisible()) await item.click();
  else await item.evaluate(element => element.click());
}

async function revealRequestAction(page, action) {
  const item = page.locator(`[data-action="${action}"]`).first();
  assert(await item.count(), `No request action exists for ${action}.`);
  await item.evaluate(element => {
    for (let parent = element.parentElement; parent; parent = parent.parentElement) {
      if (parent.tagName === 'DETAILS') parent.open = true;
    }
  });
  assert(await item.isVisible(), `Request action ${action} stayed hidden after opening its accordions.`);
  return item;
}

async function clickAdminTab(page, tab) {
  const direct = page.locator(`.side-nav [data-action="adminTab"][data-tab="${tab}"]`).first();
  if (await direct.isVisible()) {
    await direct.click();
    return;
  }
  await page.locator('[data-action="openAdminTools"]').click();
  await page.locator(`[data-action="adminToolTab"][data-tab="${tab}"]`).click();
}

async function clickProviderNav(page, tab) {
  const mobile = page.locator(`.provider-bottom-nav [data-action="providerTab"][data-tab="${tab}"]`).first();
  if (await mobile.isVisible()) {
    await mobile.click();
    return;
  }
  const desktop = page.locator(`.provider-desktop-nav [data-action="providerTab"][data-tab="${tab}"], .provider-desktop-nav [data-action="providerToolTab"][data-tab="${tab}"]`).first();
  if (await desktop.isVisible()) {
    await desktop.click();
    return;
  }
  const more = page.locator('.provider-bottom-nav [data-action="openProviderTools"], .provider-desktop-nav [data-action="openProviderTools"]').first();
  assert(await more.count(), `Provider navigation is unavailable for ${tab}.`);
  if (await more.isVisible()) await more.click();
  else await more.evaluate(element => element.click());
  const tool = page.locator(`.workspace-tools-sheet [data-action="providerToolTab"][data-tab="${tab}"]`).first();
  await tool.waitFor({ state: 'visible' });
  await tool.click();
}

(async () => {
  const testUrl = BASE_URL || await startStaticServer();
  const launchOptions = {
    headless: true,
    args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'],
  };
  if (CHROME_PATH) launchOptions.executablePath = CHROME_PATH;
  const browser = await chromium.launch(launchOptions);
  const context = await browser.newContext({
    viewport: { width: VIEWPORT_WIDTH, height: VIEWPORT_HEIGHT },
    deviceScaleFactor: 2,
    isMobile: IS_MOBILE,
    hasTouch: IS_MOBILE,
    serviceWorkers: 'block',
    locale: 'ar-OM',
    permissions: ['geolocation', 'microphone', 'notifications'],
    geolocation: { latitude: 23.61, longitude: 58.24 },
  });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.stack || error.message));
  page.on('console', message => {
    if (message.type() === 'error' && !/favicon|tile|Failed to load resource/.test(message.text())) {
      errors.push(message.text());
    }
  });

  const mockRewardCampaign = {
    id: 'ui-campaign',
    nameAr: 'مكافأة النشاط',
    nameEn: 'Activity reward',
    descriptionAr: 'أكمل الطلبات المؤكدة وتابع تقدمك.',
    descriptionEn: 'Complete confirmed requests and track your progress.',
    audience: 'user',
    rewardType: 'custom',
    rewardLabelAr: 'مكافأة تحددها الإدارة',
    rewardLabelEn: 'Management-defined reward',
    metric: 'completed_requests',
    target: 8,
    status: 'active',
    effectiveStatus: 'active',
    countdownEnabled: true,
    startsAt: '2026-01-01T00:00:00Z',
    endsAt: '2027-01-01T00:00:00Z',
    cycleMode: 'cap',
  };
  const mockUserPlatform = {
    featureFlags: { growth_hub: true, enterprise_api: false },
    organizations: [{ id: 'ui-org', name: 'مؤسسة العميل', organizationType: 'business', approvalMode: 'two_step', members: [{ id: 'm1', name: 'مستخدم الاختبار الآلي', role: 'owner' }], locations: [{ id: 'l1', name: 'الفرع الرئيسي', gov: 'مسقط', wilayah: 'السيب' }] }],
    maintenanceContracts: [{ id: 'contract-ui', providerId: 'p1', requestId: 'ui-request', title: 'صيانة كهربائية دورية', amount: 12, frequencyDays: 30, nextDueAt: '2026-09-15T08:00:00Z', status: 'active' }],
    referrals: [{ id: 'ref-ui', code: 'TEST4826', status: 'claimed', riskStatus: 'pending_review', rewardStatus: 'not_eligible' }],
    demandAlerts: [{ id: 'alert-ui', serviceValue: 'homecare|electrician', gov: 'مسقط', wilayah: 'السيب', status: 'active' }],
  };
  const mockProviderPlatform = {
    featureFlags: { growth_hub: true, enterprise_api: false },
    legalProfile: { providerId: 'p1', pathway: 'individual_omani', nationality: 'عُماني', reviewStatus: 'approved' },
    maintenanceContracts: mockUserPlatform.maintenanceContracts,
    crm: [{ id: 'crm-ui', requestId: 'ui-request', displayName: 'مستخدم الاختبار الآلي', stage: 'active', nextActionAt: '2026-08-10T08:00:00Z', invoiceStatus: 'not_issued' }],
    referrals: [{ id: 'ref-provider-ui', code: 'PRO4826', status: 'created', riskStatus: 'clear', rewardStatus: 'not_eligible' }],
    training: [{ id: 'training-ui', titleAr: 'التواصل الواضح', titleEn: 'Clear communication', contentAr: 'تأكيد النطاق والموعد قبل البدء.', contentEn: 'Confirm scope and timing before work.', passScore: 80, score: 100, status: 'passed' }],
    achievements: [{ code: 'trained_provider', earnedAt: '2026-08-01T08:00:00Z' }],
    demandAlerts: [],
  };
  const mockAdminPlatform = {
    featureFlagDetails: [{ key: 'growth_hub', enabled: true, rolloutPercentage: 100, audiences: ['user', 'provider'], config: {} }, { key: 'enterprise_api', enabled: false, rolloutPercentage: 0, audiences: ['organization'], config: {} }],
    legalReviewQueue: [{ providerId: 'p1', providerName: 'سالم البلوشي', pathway: 'individual_omani', nationality: 'عُماني', reviewStatus: 'pending', updatedAt: '2026-08-01T08:00:00Z' }],
    riskReviewQueue: [{ id: 'risk-ui', subjectKind: 'referral', subjectId: 'ref-ui', signalType: 'referral_claim', score: 20, status: 'pending_review', signals: ['human_review_before_reward'] }],
    demandGapReport: [{ serviceValue: 'homecare|electrician', gov: 'مسقط', wilayah: 'السيب', requests: 8, matched: 5, gap: 3 }],
    financialScenarios: [{ id: 'scenario-ui', name: 'سيناريو نمو محافظ', projectedRevenue: 1250, projectedCost: 760, projectedNet: 490, createdAt: '2026-08-01T08:00:00Z' }],
    integrationAdapters: [{ key: 'insurance', enabled: false, mode: 'disabled', legalStatus: 'pending', config: {} }, { key: 'government', enabled: false, mode: 'disabled', legalStatus: 'pending', config: {} }],
    enterpriseClients: [],
  };
  let mockBookingV2Enabled = null;
  let mockServicePolicies = {};
  let mockCustomerRequests = null;

  // Keep the visual smoke test deterministic while still exercising authenticated UI paths.
  await context.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    const json = body => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
    if (url.pathname === '/api/users/login') {
      return json({ token: 'ui-user-token', user: { id: 'ui-user', phone: '96895550001', name: 'مستخدم الاختبار الآلي', gov: 'مسقط', wilayah: 'السيب', pinConfigured: true } });
    }
    if (url.pathname === '/api/provider/login') {
      return json({ token: 'ui-provider-token', provider: { id: 'p1', name: 'سالم البلوشي', phone: '96891234567', gov: 'مسقط', wilayah: 'السيب', areas: ['السيب'], bio: 'كهربائي منازل بخبرة وعناية', hours: 'الأحد - الخميس: 8:00 ص - 8:00 م', status: 'available', active: true, verified: true, featured: true, mapVisible: true, location: { lat: 23.61, lng: 58.24, updatedAt: '2026-07-18T08:00:00Z' }, packageId: 'professional_12m', subscriptionState: 'active', services: [{ id: 'p1s1', catId: 'homecare', serviceId: 'electrician', priceFrom: 8, active: true, areas: ['السيب'] }], workImages: ['app-icon-512.png', 'app-icon-192.png'], documents: [], rating: 4.9, reviews: 12, qualityScore: 94, pinConfigured: true } });
    }
    if (url.pathname === '/api/auth/refresh' || url.pathname === '/api/auth/persist') {
      const payload = route.request().postDataJSON();
      const kind = payload.kind;
      if (kind === 'provider') return json({ token: 'ui-provider-token', sessionKind: 'provider', session: { kind: 'provider', providerId: 'p1', name: 'سالم البلوشي' } });
      if (kind === 'user') return json({ token: 'ui-user-token', sessionKind: 'user', session: { kind: 'user', userId: 'ui-user', name: 'مستخدم الاختبار الآلي' } });
      if (kind === 'admin') return json({ token: 'ui-admin-token', sessionKind: 'admin', session: { kind: 'admin', id: 'ui-admin', role: 'super_admin' } });
    }
    if (url.pathname === '/api/auth/logout') return json({ ok: true, revoked: true });
    if (url.pathname === '/api/provider/profile') return json({});
    if (url.pathname === '/api/provider/quote-templates') {
      const payload = route.request().postDataJSON();
      return json({ ok: true, templates: payload.templates || [] });
    }
    if (url.pathname === '/api/provider/support') return json({ ok: true, notificationId: 'ui-provider-support' });
    if (url.pathname === '/api/admin/login') return json({ token: 'ui-admin-token', user: { id: 'ui-admin', name: 'إدارة خدماتي', role: 'super_admin' } });
    if (url.pathname === '/api/platform') {
      const auth = route.request().headers().authorization || '';
      const platform = auth.includes('provider') ? mockProviderPlatform : mockUserPlatform;
      return json({ ok: true, result: platform, platform, serverTime: '2026-08-01T08:00:00Z' });
    }
    if (url.pathname === '/api/admin/platform') return json({ ok: true, result: mockAdminPlatform, platform: mockAdminPlatform, serverTime: '2026-08-01T08:00:00Z' });
    if (url.pathname === '/api/admin/session') return json({ adminEntities: { rewardCampaigns: [mockRewardCampaign], campaignEligibility: [] }, platform: mockAdminPlatform });
    if (url.pathname === '/api/bootstrap') {
      const auth = route.request().headers().authorization || '';
      return json({
        platform: auth.includes('provider') ? mockProviderPlatform : mockUserPlatform,
        ...(typeof mockBookingV2Enabled === 'boolean' ? { bookingV2Enabled: mockBookingV2Enabled } : {}),
        servicePolicies: mockServicePolicies,
        ...(Array.isArray(mockCustomerRequests) ? { customerRequests: mockCustomerRequests } : {}),
      });
    }
    if (url.pathname === '/api/push/public-key') return json({ publicKey: '' });
    return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: 'request_failed' }) });
  });
  await page.goto(testUrl, { waitUntil: 'domcontentloaded' });
  await page.evaluate(() => localStorage.clear());
  try {
    await page.reload({ waitUntil: 'domcontentloaded' });
  } catch (error) {
    if (!/ERR_ABORTED|frame was detached/i.test(String(error))) throw error;
    await page.waitForLoadState('domcontentloaded');
  }
  await page.waitForSelector('[data-action="openUserLogin"]');
  await page.waitForTimeout(180);
  if (await page.locator('.role-onboarding').count()) {
    assert(await page.locator('.role-onboarding .onboarding-dot').count() === 3, 'First-open onboarding must contain three concise steps.');
    await capture(page, '00a-first-open-onboarding', { fullPage: false });
    await page.locator('[data-action="skipOnboarding"]').click();
  }
  await capture(page, '00-entry');
  if (IS_MOBILE && VIEWPORT_HEIGHT > 700) {
    const entryLayout = await page.locator('.access-stage').evaluate(card => {
      const cardBox = card.getBoundingClientRect();
      const utilities = card.querySelector('.access-utility')?.getBoundingClientRect();
      const trust = card.querySelector('.access-assurance')?.getBoundingClientRect();
      return {
        cardHeight: cardBox.height,
        viewportHeight: window.innerHeight,
        topGap: utilities ? utilities.top - cardBox.top : 999,
        bottomGap: trust ? cardBox.bottom - trust.bottom : 999,
      };
    });
    assert(entryLayout.cardHeight <= entryLayout.viewportHeight - 8, 'The mobile entry card extends beyond the usable screen height.');
    assert(entryLayout.topGap <= 32 && entryLayout.bottomGap <= 36, 'The mobile entry content leaves an excessive blank band at the top or bottom.');
  }

  await page.locator('[data-action="openUserLogin"]').click();
  await capture(page, '00c-user-login', { fullPage: false });
  await page.locator('#customerLoginPhone').click();
  assert(await page.locator('#customerLoginPhone').isVisible(), 'Clicking inside the sign-in sheet closed it unexpectedly.');
  assert(await page.locator('#customerLoginName').count() === 0, 'Sign-in must not ask for registration details.');
  await page.locator('[data-action="openUserRegistration"]').click();
  await capture(page, '00d-user-registration', { fullPage: false });
  assert(await page.locator('#customerRegisterName').isVisible(), 'The separate user registration sheet did not open.');
  assert(await page.locator('body.modal-open').count() === 1, 'Registration must lock the page behind the active sheet.');
  if (IS_MOBILE) {
    const registrationViewport = await page.locator('.registration-flow').evaluate(flow => ({
      height: flow.getBoundingClientRect().height,
      viewport: window.innerHeight,
      scrollable: flow.querySelector('.auth-flow-body').scrollHeight >= flow.querySelector('.auth-flow-body').clientHeight,
    }));
    assert(Math.abs(registrationViewport.height - registrationViewport.viewport) <= 2, 'Mobile registration does not own the full viewport.');
    assert(registrationViewport.scrollable, 'Registration content is not scrollable inside its foreground sheet.');
  }
  assert(await page.locator('.registration-progress [data-action="userRegistrationStep"]').count() === 3, 'User registration must use three focused stages.');
  assert(await page.locator('#customerRegisterNationality').count(), 'User registration is missing its nationality field.');
  await page.locator('.registration-flow [data-action="openUserLogin"]').click();
  await page.locator('#customerLoginPhone').fill('95550001');
  await page.locator('[data-action="customerLogin"]').click();
  assert(await page.locator('#customerLoginPin').isVisible(), 'Submitting without a PIN must keep the sign-in sheet open.');
  await page.locator('#customerLoginPin').fill('2468');
  await page.locator('[data-action="customerLogin"]').click();
  await page.waitForSelector(IS_MOBILE ? '.role-onboarding' : '.role-onboarding,.app-top');
  const onboardingVisible = await page.locator('.role-onboarding').isVisible().catch(() => false);
  if (onboardingVisible) {
    const onboardingImage = page.locator('.role-onboarding .onboarding-visual img');
    assert(/assets\/onboarding\/core\//.test(await onboardingImage.getAttribute('src')), 'The square role onboarding image is missing.');
    await onboardingImage.evaluate(image => image.complete ? true : new Promise(resolve => image.addEventListener('load', () => resolve(true), { once: true })));
    assert(await onboardingImage.evaluate(image => image.naturalWidth >= 900 && image.naturalHeight >= 900 && Math.abs(image.naturalWidth - image.naturalHeight) <= 2), 'The onboarding image is not a high-resolution square launch asset.');
    assert(await onboardingImage.evaluate(image => getComputedStyle(image).objectFit === 'cover'), 'Mobile onboarding artwork must fill the square frame without side gaps.');
  } else {
    assert(!IS_MOBILE && await page.locator('.app-top').isVisible(), 'Desktop sign-in reached neither onboarding nor the authenticated application.');
  }
  const onboardingSets = [
    { role: 'user', slides: ['user-service', 'user-direct-request', 'user-matching', 'user-track'] },
    { role: 'guest', slides: ['guest-browse', 'guest-compare', 'guest-signin', 'guest-privacy'] },
    { role: 'provider', slides: ['provider-account-v2', 'provider-community-v2', 'provider-today-v2', 'provider-tasks-v2'] },
    { role: 'company', slides: ['company-profile', 'company-dispatch', 'company-analytics', 'company-team'] },
  ].map(set => ({ ...set, slides: set.slides.map(name => `assets/onboarding/core/${name}.webp`) }));
  for (const set of onboardingSets) {
    assert(set.slides.length === 4, `${set.role} onboarding must contain four focused steps.`);
    assert(set.slides.every(src => /assets\/onboarding\/core\//.test(src)), `${set.role} onboarding is using an outdated image.`);
    assert(new Set(set.slides).size === set.slides.length, `${set.role} onboarding repeats the same artwork.`);
  }
  assert(new Set(onboardingSets.flatMap(set => set.slides)).size === 16, 'Every onboarding state must use its own artwork.');
  const launchSources = [
    ...new Set(onboardingSets.flatMap(set => set.slides)),
    'assets/ads/campaigns/home-services.webp',
    'assets/ads/campaigns/nearby-services.webp',
    'assets/ads/campaigns/business-services.webp',
  ];
  const launchHtml = await page.content();
  assert(launchSources.every(src => launchHtml.includes(src)), 'The production page is not wired to every current launch image.');
  const launchImages = await page.evaluate(async sources => {
    return Promise.all(sources.map(async src => ({ src, ok: (await fetch(src, { cache: 'no-store' })).ok })));
  }, launchSources);
  assert(launchImages.every(item => item.ok), `A launch image failed to load: ${launchImages.filter(item => !item.ok).map(item => item.src).join(', ')}`);
  if (onboardingVisible) {
    await capture(page, '00b-user-onboarding');
    await page.locator('[data-action="skipOnboarding"]').click();
  }
  assert(await page.locator('#toastRoot .toast').count() === 0, 'A validation toast remained visible after successful sign-in.');
  const persistedUserAuth = await page.evaluate(() => JSON.parse(sessionStorage.getItem('KHADAMATI_AUTH_V3') || '{}'));
  assert(persistedUserAuth.userToken === 'ui-user-token', 'User authentication was not persisted for the next app launch.');
  const rememberedUser = await page.evaluate(() => JSON.parse(localStorage.getItem('KHADAMATI_ACCOUNT_MEMORY_V1') || '{}'));
  assert(rememberedUser.user?.id === 'ui-user', 'The device did not retain the safe user account identity.');
  const userNavOrder = await page.locator('.bottom-nav [data-action="nav"]').evaluateAll(items => items.map(item => item.dataset.view));
  assert(JSON.stringify(userNavOrder) === JSON.stringify(['home', 'services', 'tasks', 'community', 'myAccount']), `User bottom navigation order is incorrect: ${userNavOrder.join(', ')}`);
  assert((await page.locator('.app-brand .brand-word > span').textContent()).trim() === 'خدماتي', 'The Arabic app name is missing from the signed-in header.');

  await page.evaluate(() => {
    const key = 'KHADAMATI_PRIVATE_STATE_V1';
    const state = JSON.parse(sessionStorage.getItem(key) || '{}');
    state.notifications = [{
      id: 'ui-required-action', type: 'request', title: 'إجراء مطلوب', message: 'راجع الطلب',
      target: 'user', targetId: 'ui-user', relatedId: 'ui-required-request', entityId: 'ui-required-request',
      actionKind: 'open_booking', actionRoute: 'user:request:ui-required-request', requiresAction: true,
      stateVersion: 1, priority: 'high', read: true, readAt: new Date().toISOString(), createdAt: new Date().toISOString(), _serverVerified: true,
    }, ...(state.notifications || []).filter(item => item.id !== 'ui-required-action')];
    sessionStorage.setItem(key, JSON.stringify(state));
  });
  const permissionSession = await browser.newBrowserCDPSession();
  const { browserContextIds } = await permissionSession.send('Target.getBrowserContexts');
  const permissionContextId = browserContextIds[browserContextIds.length - 1];
  const testOrigin = new URL(testUrl).origin;
  await context.clearPermissions();
  await permissionSession.send('Browser.setPermission', { permission: { name: 'notifications' }, setting: 'denied', origin: testOrigin, browserContextId: permissionContextId });
  await page.reload({ waitUntil: 'domcontentloaded' });
  assert(await page.evaluate(() => Notification.permission) === 'denied', 'Notification permission denial was not applied for the internal-card check.');
  await page.waitForSelector('#actionPromptRoot .action-prompt-card');
  assert(await page.locator('.bottom-nav [data-view="tasks"] .nav-count').count(), 'A read-but-unresolved action is missing from the requests badge.');
  await page.evaluate(() => localStorage.setItem('KHADAMATI_DIRECT_REQUEST_GUIDE_V1', '1'));
  await clickFirstAction(page, 'quickRequestForm');
  await page.locator('#qrNote').evaluate(input => { input.value = 'مدخل يجب ألا يضيع عند وصول الإشعار'; });
  await page.waitForSelector('#actionPromptRoot.is-inline .action-prompt-card');
  assert(await page.locator('#actionPromptRoot.is-inline .action-prompt-card').isVisible(), 'Action prompt is hidden behind the active form.');
  await page.locator('#actionPromptRoot [data-action="openActionPrompt"]').evaluate(button => button.click());
  await page.waitForTimeout(100);
  assert(await page.locator('#qrNote').inputValue() === 'مدخل يجب ألا يضيع عند وصول الإشعار', 'Opening an action prompt destroyed the active request form.');
  await page.evaluate(() => sessionStorage.removeItem('KHADAMATI_PENDING_NOTIFICATION_V1'));
  await page.locator('.request-modal [data-action="closeModal"]').click();
  await page.waitForTimeout(80);
  await page.locator('#actionPromptRoot [data-action="snoozeActionPrompt"]').click();
  assert(await page.locator('#actionPromptRoot .action-prompt-card').count() === 0, 'Later did not defer the current action prompt.');
  assert(await page.locator('.bottom-nav [data-view="tasks"] .nav-count').count(), 'Later incorrectly cleared the unresolved action badge.');
  await page.locator('.app-top [data-action="openNotifications"]').click();
  const requiredCard = page.locator('.notification-disclosure:has(summary[data-id="ui-required-action"])');
  assert(await requiredCard.count(), 'The deferred action disappeared from notification history.');
  assert(await requiredCard.locator('[data-action="deleteNotification"]').count() === 0, 'An unresolved required action can still be deleted.');
  await page.locator('.notification-center-sheet [data-action="closeModal"]').click();
  await page.evaluate(() => {
    const key = 'KHADAMATI_PRIVATE_STATE_V1';
    const state = JSON.parse(sessionStorage.getItem(key) || '{}');
    state.notifications = (state.notifications || []).filter(item => item.id !== 'ui-required-action');
    sessionStorage.setItem(key, JSON.stringify(state));
    sessionStorage.removeItem('KHADAMATI_ACTION_PROMPT_SNOOZED_V1');
  });
  await context.grantPermissions(['geolocation', 'microphone', 'notifications'], { origin: testOrigin });
  await page.reload({ waitUntil: 'domcontentloaded' });

  await page.evaluate(() => sessionStorage.setItem('KHADAMATI_INSTANT_POLICY_TEST_BACKUP', sessionStorage.getItem('KHADAMATI_PRIVATE_STATE_V1') || '{}'));
  const instantPolicy = { categoryId: 'homecare', serviceId: 'electrician' };
  mockBookingV2Enabled = true;
  mockServicePolicies = { 'homecare|electrician': { fulfillmentMode: 'instant' } };
  const flaggedBootstrap = page.waitForResponse(response => new URL(response.url()).pathname === '/api/bootstrap');
  await page.reload({ waitUntil: 'domcontentloaded' });
  await flaggedBootstrap;
  await page.waitForTimeout(100);
  await clickUserNav(page, 'services');
  await page.locator(`[data-action="servicesCategory"][data-cat="${instantPolicy.categoryId}"]`).first().click();
  await page.locator(`[data-action="serviceSheet"][data-cat="${instantPolicy.categoryId}"][data-service="${instantPolicy.serviceId}"]`).click();
  assert(await page.locator('.modal [data-action="openInstantBooking"]').count(), 'Flagged instant service did not expose server-confirmed slot selection.');
  await page.locator('.modal [data-action="closeModal"]').click();
  mockBookingV2Enabled = false;
  const disabledBootstrap = page.waitForResponse(response => new URL(response.url()).pathname === '/api/bootstrap');
  await page.reload({ waitUntil: 'domcontentloaded' });
  await disabledBootstrap;
  await page.waitForTimeout(100);
  await clickUserNav(page, 'services');
  await page.locator(`[data-action="servicesCategory"][data-cat="${instantPolicy.categoryId}"]`).first().click();
  await page.locator(`[data-action="serviceSheet"][data-cat="${instantPolicy.categoryId}"][data-service="${instantPolicy.serviceId}"]`).click();
  assert(await page.locator('.modal [data-action="quickRequestForService"]').count(), 'Flag-off service did not fall back to the quoted request flow.');
  assert(await page.locator('.modal [data-action="openInstantBooking"]').count() === 0, 'Instant booking bypassed the server feature flag.');
  await page.locator('.modal [data-action="closeModal"]').click();
  mockBookingV2Enabled = null;
  mockServicePolicies = {};
  await page.evaluate(() => {
    const backup = sessionStorage.getItem('KHADAMATI_INSTANT_POLICY_TEST_BACKUP');
    if (backup) sessionStorage.setItem('KHADAMATI_PRIVATE_STATE_V1', backup);
    sessionStorage.removeItem('KHADAMATI_INSTANT_POLICY_TEST_BACKUP');
  });
  await page.reload({ waitUntil: 'domcontentloaded' });

  assert((await page.locator('.clean-grid .category-tile').count()) <= 6, 'Home must show no more than six categories.');
  assert(await page.locator('main.view > .home-ad.ad-slider').count(), 'Advertisement slider must be the first home block.');
  const adCopyFit = await page.locator('.home-ad.ad-slider').evaluate(ad => {
    const copy = ad.querySelector('.ad-slider-copy');
    if (!copy || copy.hidden) return true;
    const adBox = ad.getBoundingClientRect();
    const copyBox = copy.getBoundingClientRect();
    const style = getComputedStyle(copy);
    const alpha = Number(style.backgroundColor.match(/[\d.]+(?=\))/g)?.at(-1) || 1);
    return copyBox.height <= adBox.height * 0.46
      && copyBox.width <= adBox.width * 0.8
      && alpha < 0.8;
  });
  assert(adCopyFit, 'Advertisement copy is oversized or hides too much of the image.');
  assert((await page.locator('.popular-rail').count()) === 0, 'Popular services rail should be removed from home.');
  assert((await page.locator('.offline-sync-card').count()) === 0, 'Offline queue banner should not crowd the home page.');
  assert(await page.locator('.direct-request-card').count(), 'Direct request card is missing.');
  assert((await page.locator('.global-search').count()) === 0, 'Duplicated global search should be removed.');
  assert((await page.locator('main[data-view="home"] .provider-listing').count()) === 0, 'Home should not contain provider recommendation cards.');
  await capture(page, '01-user-home');

  await clickUserNav(page, 'services');
  assert(await page.locator('.app-back:visible').count() === 1, 'Services should show only the global back button.');
  const servicesRail = page.locator('#servicesCategoryRail');
  assert(await servicesRail.count(), 'Services category rail is missing.');
  const railMetrics = await servicesRail.evaluate(async element => {
    const chips = [...element.querySelectorAll('.chip-tab')];
    const before = element.scrollLeft;
    const distance = Math.min(220, Math.max(0, element.scrollWidth - element.clientWidth));
    element.scrollLeft = getComputedStyle(element).direction === 'rtl' ? -distance : distance;
    await new Promise(resolve => requestAnimationFrame(() => resolve()));
    return {
      before,
      after: element.scrollLeft,
      overflow: getComputedStyle(element).overflowX,
      touchAction: getComputedStyle(element).touchAction,
      scrollWidth: element.scrollWidth,
      clientWidth: element.clientWidth,
      chipsFit: chips.every(chip => {
        const rect = chip.getBoundingClientRect();
        return Number(getComputedStyle(chip).flexShrink) === 0 && rect.width >= 82 && chip.scrollWidth <= chip.clientWidth + 1;
      }),
    };
  });
  assert(['auto', 'scroll'].includes(railMetrics.overflow), 'Services categories are not horizontally scrollable.');
  assert(railMetrics.scrollWidth > railMetrics.clientWidth, 'Services rail should overflow instead of shrinking every category.');
  assert(Math.abs(railMetrics.after - railMetrics.before) > 20, `Services category rail did not respond to horizontal scrolling: ${JSON.stringify(railMetrics)}`);
  assert(/pan-x|auto/.test(railMetrics.touchAction), 'Services rail does not allow a horizontal touch gesture.');
  assert(railMetrics.chipsFit, 'A services category label is clipped or its chip is being compressed.');
  await page.locator('[data-action="servicesCategory"][data-cat="cleaning"]').click();
  assert(await page.locator('[data-action="servicesCategory"][data-cat="cleaning"][aria-selected="true"]').count(), 'Selecting a services category did not update the active tab.');
  assert(await page.locator('.services-category-panel').count() === 1, 'Services should render one selected category rather than every category at once.');
  const servicePictograms = await page.locator('.services-service-grid .service-tile .kh-subject-art').evaluateAll(items => items.map(item => item.dataset.pictogram));
  assert(servicePictograms.length >= 10, 'Semantic service artwork is missing from the services grid.');
  assert(new Set(servicePictograms).size >= 7, 'Service artwork is still repeating one generic symbol across unrelated services.');
  const serviceCardMetrics = await page.locator('.services-service-grid .service-tile').evaluateAll(cards => cards.every(card => {
    const rect = card.getBoundingClientRect();
    const label = card.querySelector('strong');
    return rect.width >= 78 && rect.left >= -1 && rect.right <= window.innerWidth + 1 && label && label.getBoundingClientRect().right <= rect.right + 1;
  }));
  assert(serviceCardMetrics, 'A service card or label overflows the small-screen grid.');
  await capture(page, '01a-mobile-services');
  await clickUserNav(page, 'home');

  await clickUserNav(page, 'search');
  assert(await page.locator('.search-map-banner').count(), 'Search from map banner is missing.');
  assert(await page.locator('.app-back:visible').count() === 1, 'Search should show only the global back button.');
  assert((await page.locator('.search-filter-panel').count()) === 0, 'Advanced filters should start collapsed.');
  await page.locator('[data-action="searchCategory"]').first().click();
  assert(await page.locator('.service-choice-grid').count(), 'Service stage did not open after choosing a category.');
  const serviceOverflow = await page.locator('.service-choice-grid').evaluate(element => getComputedStyle(element).overflowX);
  assert(['auto', 'scroll'].includes(serviceOverflow), 'Exact services should scroll horizontally.');
  const searchColumns = await page.locator('.search-results-grid').evaluate(element => getComputedStyle(element).gridTemplateColumns.split(' ').length);
  assert(searchColumns === (IS_MOBILE ? 2 : 3), 'Search results grid does not match the active viewport.');
  assert(await page.locator('.search-results-grid [data-action="directWhatsapp"]').count() === 0, 'Public provider cards must not expose direct WhatsApp.');
  const providerControlsFit = await page.locator('.search-results-grid .provider-listing').evaluateAll(cards => cards.every(card => {
    const bounds = card.getBoundingClientRect();
    return [...card.querySelectorAll('.provider-card-title-row .status,[data-action="providerDetails"]')].every(control => {
      const rect = control.getBoundingClientRect();
      return rect.left >= bounds.left - 1 && rect.right <= bounds.right + 1;
    });
  }));
  assert(providerControlsFit, 'Provider status or details button overflows its card.');
  const providerContentMetrics = await page.locator('.search-results-grid .provider-listing').evaluateAll(cards => cards.map(card => {
    const media = card.querySelector('.listing-media')?.getBoundingClientRect();
    const title = card.querySelector('.listing-title')?.getBoundingClientRect();
    const price = card.querySelector('.listing-price')?.getBoundingClientRect();
    const status = card.querySelector('.provider-card-title-row .status')?.getBoundingClientRect();
    const favourite = card.querySelector('.heart')?.getBoundingClientRect();
    if (!media || !title || !price || !status || !favourite) return { ok: false, reason: 'missing element' };
    const controlsOverlap = !(status.right <= favourite.left || status.left >= favourite.right || status.bottom <= favourite.top || status.top >= favourite.bottom);
    const usesMobileOverlay = window.matchMedia('(max-width: 760px)').matches;
    return {
      ok: (usesMobileOverlay
        ? title.top >= media.top + media.height * 0.34 && title.bottom <= media.bottom + 1 && price.top >= title.top && price.bottom <= media.bottom + 1
        : title.top >= media.bottom - 1 && price.top >= title.top) && status.top >= media.top - 1 && status.bottom <= media.bottom + 1 && !controlsOverlap,
      mode: usesMobileOverlay ? 'mobile-overlay' : 'desktop-body',
      media: { top: media.top, bottom: media.bottom },
      title: { top: title.top, bottom: title.bottom },
      price: { top: price.top, bottom: price.bottom },
      status: { top: status.top, bottom: status.bottom },
      favourite: { top: favourite.top, bottom: favourite.bottom },
      controlsOverlap,
    };
  }));
  assert(providerContentMetrics.every(item => item.ok), `Provider card overlay is not readable or leaves too little portrait space: ${JSON.stringify(providerContentMetrics)}`);
  assert(await page.locator('.search-results-grid .provider-card-title-row .status.off').count() === 0, 'Unavailable providers must stay hidden from public search.');
  const firstProviderImage = page.locator('.search-results-grid .provider-listing .listing-media img').first();
  assert(/assets\/providers\/omani-electrician\.webp/.test(await firstProviderImage.getAttribute('src')), 'The launch provider card is still using a generated placeholder.');
  const providerImageLoaded = await firstProviderImage.evaluate(image => image.complete
    ? image.naturalWidth >= 800
    : new Promise(resolve => {
      image.addEventListener('load', () => resolve(image.naturalWidth >= 800), { once: true });
      image.addEventListener('error', () => resolve(false), { once: true });
    }));
  assert(providerImageLoaded, 'The launch provider image did not load at production quality.');
  await capture(page, '01b-progressive-search');
  await clickUserNav(page, 'home');

  await page.evaluate(() => {
    const key = 'KHADAMATI_PRIVATE_STATE_V1';
    const state = JSON.parse(sessionStorage.getItem(key) || '{}');
    const expiresAt = new Date(Date.now() + 24 * 86400000).toISOString();
    state.communityListings = [
      {
        id: 'community-package-ui',
        kind: 'package',
        ownerKind: 'provider',
        ownerId: 'p-electric',
        title: 'باقة العناية الكهربائية للمنزل',
        description: 'فحص لوحة الكهرباء ونقاط التوصيل مع تقرير مختصر وموعد مؤكد.',
        categoryId: 'homecare',
        serviceValue: 'homecare|electrician',
        priceAmount: 12,
        billingPeriod: 'one_time',
        durationText: 'زيارة واحدة · ساعتان',
        gov: 'مسقط',
        wilayah: 'السيب',
        details: {
          inclusions: ['فحص اللوحة', 'فحص نقاط الكهرباء'],
          commitment: 'موعد مؤكد داخل التطبيق',
        },
        contactChannels: ['app', 'whatsapp'],
        status: 'active',
        billingStatus: 'free_first',
        featured: true,
        mine: false,
        expiresAt,
        createdAt: new Date().toISOString(),
        updatedAt: new Date().toISOString(),
        owner: {
          id: 'p-electric',
          name: 'جهـاد للتقنية',
          imageUrl: 'assets/providers/omani-electrician.webp',
          gov: 'مسقط',
          wilayah: 'السيب',
          verified: true,
          whatsappAvailable: true,
        },
      },
      {
        id: 'community-wanted-ui',
        kind: 'wanted',
        ownerKind: 'user',
        ownerId: 'ui-user',
        title: 'أبحث عن كهربائي لتركيب إنارة',
        description: 'تركيب وحدتي إنارة وفحص المفتاح في المنزل.',
        categoryId: 'homecare',
        serviceValue: 'homecare|electrician',
        budgetMin: 10,
        budgetMax: 18,
        durationText: 'خلال يومين',
        gov: 'مسقط',
        wilayah: 'السيب',
        status: 'active',
        billingStatus: 'included',
        featured: false,
        mine: true,
        offers: [],
        expiresAt,
        createdAt: new Date().toISOString(),
        updatedAt: new Date().toISOString(),
        owner: {
          id: 'ui-user',
          name: 'مستخدم خدماتي',
          imageUrl: '',
          gov: 'مسقط',
          wilayah: 'السيب',
          verified: true,
        },
      },
    ];
    state.communityStats = { activePackages: 1, activeWanted: 1, openReports: 0 };
    state.communityTab = 'packages';
    sessionStorage.setItem(key, JSON.stringify(state));
  });
  await page.reload({ waitUntil: 'domcontentloaded' });
  await clickUserNav(page, 'community');
  assert(await page.locator('.community-page').count(), 'The Community destination did not open.');
  assert(await page.locator('.community-v3-tabs [data-value="packages"]').count(), 'The packages Community tab is missing.');
  assert(await page.locator('.community-v3-tabs [data-value="board"]').count(), 'The merged request-board Community tab is missing.');
  assert(await page.locator('.community-card').count() === 1, 'Community package card is missing.');
  await capture(page, '08-community-user-packages', { fullPage: false });
  await page.locator('[data-action="toggleLang"]:visible').first().click();
  assert(await page.locator('html').getAttribute('dir') === 'ltr', 'Community English mode did not switch to LTR.');
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), 'Community English mode overflows horizontally.');
  await capture(page, '08h-community-user-english', { fullPage: false });
  await page.locator('[data-action="toggleLang"]:visible').first().click();
  await page.evaluate(() => document.documentElement.dataset.theme = 'dark');
  await capture(page, '08i-community-user-dark', { fullPage: false });
  await page.evaluate(() => document.documentElement.dataset.theme = 'light');
  await page.locator('[data-action="openCommunityListing"]').first().click();
  await capture(page, '08a-community-package-details', { fullPage: false });
  await page.locator('.community-detail-sheet [data-action="closeModal"]').click();
  await page.locator('.community-v3-tabs [data-value="board"]').click();
  assert(await page.locator('.community-need-card').count() >= 1, 'The unified Community request feed is empty.');
  assert(await page.locator('.community-board-unified').count(), 'The merged request board did not open.');
  assert(await page.locator('.bottom-nav [data-view="community"][aria-current="page"]').count(), 'Community is not represented as its active bottom-navigation destination.');
  await capture(page, '08b-community-user-wanted', { fullPage: false });
  await page.locator('.community-fab').click();
  assert(await page.locator('.direct-request-guide,.request-wizard').count(), 'The Community add action did not open the central request journey.');
  await capture(page, '08c-community-wanted-editor', { fullPage: false });
  await page.locator('#modalRoot [data-action="closeModal"]').click();
  await capture(page, '08d-community-request-board', { fullPage: false });
  assert(await page.locator('.community-board-unified').evaluate(element => element.getBoundingClientRect().right <= window.innerWidth + 1), 'Unified request board overflows the mobile viewport.');
  await clickUserNav(page, 'home');
  await page.locator('.direct-request-card [data-action="quickRequestForm"]').click();
  await page.waitForTimeout(150);
  if (await page.locator('.direct-request-guide').count()) {
    const directGuideImage = page.locator('.direct-request-guide img');
    assert(/assets\/onboarding\/core\/user-direct-request\.webp/.test(await directGuideImage.getAttribute('src')), 'Direct-request guidance artwork is missing.');
    await page.locator('[data-action="continueDirectRequestGuide"]').click();
    await page.waitForTimeout(150);
  }
  assert(await page.locator('.request-wizard').count(), `Direct request did not open: ${(await page.locator('#toast').textContent().catch(() => '')) || errors.join(' | ') || 'no visible message'}`);
  await capture(page, '01c-direct-service');
  const requestCategoryColumns = await page.locator('.category-availability-grid').evaluate(grid => getComputedStyle(grid).gridTemplateColumns.split(' ').length);
  assert(requestCategoryColumns === 3, 'Direct-request categories should use three compact cards per row on phone.');
  await page.locator('[data-action="requestSelectCategory"].available').first().click();
  await page.waitForSelector('[data-action="requestSelectService"]');
  const requestServiceColumns = await page.locator('.service-availability-grid').evaluate(grid => getComputedStyle(grid).gridTemplateColumns.split(' ').length);
  assert(requestServiceColumns === 3, 'Direct-request services should use three compact cards per row on phone.');
  await page.locator('[data-action="requestSelectService"].available').first().click();
  await page.waitForSelector('.request-wizard[data-step="2"]');
  assert(Boolean(await page.locator('#qrCategory').inputValue()), 'Available category was not selected.');
  assert(Boolean(await page.locator('#qrService').inputValue()), 'Available service was not selected.');
  assert(await page.locator('.request-selection-strip').count(), 'The location step is missing the selected service summary.');
  const requestReachability = await page.locator('.request-modal').evaluate(modal => {
    const body = modal.querySelector('.modal-body');
    const actions = modal.querySelector('.request-wizard-step.active .wizard-actions');
    return {
      overflowY: getComputedStyle(body).overflowY,
      actionsPosition: getComputedStyle(actions).position,
      modalFits: modal.getBoundingClientRect().height <= window.innerHeight + 1,
    };
  });
  assert(['auto', 'scroll'].includes(requestReachability.overflowY), 'Direct request content cannot scroll on a small phone.');
  assert(!['fixed', 'sticky'].includes(requestReachability.actionsPosition), `Direct-request actions should not float over form content (${requestReachability.actionsPosition}).`);
  assert(requestReachability.modalFits, 'Direct request exceeds the phone viewport.');
  const selectedServiceBeforeBack = await page.locator('#qrService').inputValue();
  await page.locator('.request-step-top-back[data-action="requestWizardBack"]').click();
  assert(await page.locator('.request-wizard[data-step="1"]').count(), 'Selected-service back control did not return to service choice.');
  assert(await page.locator('#qrService').inputValue() === selectedServiceBeforeBack, 'Returning to service choice lost the selected service.');
  await page.locator(`[data-action="requestSelectService"][data-value="${selectedServiceBeforeBack}"]`).click();
  await page.waitForSelector('.request-wizard[data-step="2"]');
  assert(await page.locator('.request-location-stage').count(), 'Location step is missing from direct request.');
  await capture(page, '01d-direct-location');
  const selectedServiceBeforeMap = await page.locator('#qrService').inputValue();
  const selectedGovernorateBeforeMap = await page.locator('#qrGov').inputValue();
  await page.locator('.request-wizard-step.active [data-action="openRequestLocationMap"]').click();
  await page.waitForSelector('.request-map-picker .leaflet-live-map[data-selectable="1"]');
  await page.locator('.request-map-picker [data-action="resumeRequestLocation"]').click();
  assert(await page.locator('.request-wizard[data-step="2"]').count(), 'Closing the request map should return to the location step only.');
  assert(await page.locator('#qrService').inputValue() === selectedServiceBeforeMap, 'Closing the request map lost the chosen service.');
  assert(await page.locator('#qrGov').inputValue() === selectedGovernorateBeforeMap, 'Closing the request map lost the chosen governorate.');
  await page.locator('.request-wizard-step.active [data-action="openRequestLocationMap"]').click();
  await page.waitForSelector('.request-map-picker .leaflet-live-map[data-selectable="1"]');
  await page.locator('.request-map-picker .leaflet-live-map').click({ position: { x: 170, y: 170 } });
  await page.waitForFunction(() => Boolean(document.querySelector('#mapPickLat')?.value && document.querySelector('#mapPickLng')?.value));
  await page.locator('[data-action="usePickedRequestLocation"]').click();
  const mapResumeState = await page.evaluate(() => ({
    wizardStep: document.querySelector('.request-wizard')?.dataset.step || '',
    mapOpen: Boolean(document.querySelector('.request-map-picker')),
    modalClass: document.querySelector('#modalRoot .modal')?.className || '',
  }));
  assert(mapResumeState.wizardStep === '2' && !mapResumeState.mapOpen, `Map selection should resume at the location step: ${JSON.stringify(mapResumeState)}`);
  assert(await page.locator('#qrService').inputValue() === selectedServiceBeforeMap, 'Map selection lost the chosen service.');
  assert(Boolean(await page.locator('#qrLocation').inputValue()), 'Selected map point was not saved to the request.');
  await page.locator('[data-action="requestWizardNext"][data-step="3"]:visible').click();
  await page.locator('#qrNote').fill('أحتاج تنفيذ هذه الخدمة في المنزل خلال هذا الأسبوع');
  await page.locator('[data-action="requestWizardNext"][data-step="4"]:visible').click();
  assert(await page.locator('.match-summary').count(), 'Request matching summary is missing.');
  assert(await page.locator('.request-preview .preview-grid > div').count() === 4, 'Request review summary should fill its four-card grid.');
  await capture(page, '01g-direct-review');
  await page.locator('[data-action="saveQuickRequest"]').click();
  await page.waitForSelector('.active-request-home');
  await clickUserNav(page, 'community');
  await page.locator('.community-v3-tabs [data-value="board"]').click();
  assert(await page.locator('.community-need-card').count(), 'New request is missing from the unified request board.');
  await clickUserNav(page, 'myAccount');
  assert(await page.locator('.app-back:visible').count() === 1, 'My Account shows a duplicate back control.');
  assert(await page.locator('.account-profile-card [data-action="editAccount"] svg').count() === 1, 'Account edit action is not using the familiar edit icon.');
  assert((await page.locator('.account-menu [data-action="editAccount"] b').textContent()).trim() === 'إعدادات الحساب', 'Account settings label is still long or unclear.');
  assert(await page.locator('.requests-disclosure').count(), 'Grouped request sections are missing from My Account.');
  assert(await page.locator('.requests-disclosure[open]').count() === 0, 'Request groups should start collapsed.');
  await capture(page, '01i-account', { fullPage: false });
  await page.locator('.requests-disclosure summary').first().click();
  assert(await page.locator('.requests-disclosure[open] .request-item-disclosure').count(), 'Created request is missing from the active request section.');
  assert((await page.locator('.requests-disclosure[open] .request-disclosure-main small').first().textContent()).trim().length > 0, 'Request date and time are missing from the customer request summary.');
  assert(await page.locator('.requests-disclosure[open] [data-action="repeatRequest"]').count() === 0, 'An active request should not offer a duplicate repeat action.');
  await clickFirstAction(page, 'openUserOperationsCenter');
  await page.waitForSelector('.platform-center-sheet');
  assert(await page.locator('.platform-center-sheet .platform-summary-card').count() >= 2, 'User operations center is missing organization and maintenance summaries.');
  assert(await page.locator('.platform-center-sheet').evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), 'User operations center overflows the mobile viewport.');
  await capture(page, '01h-user-operations', { fullPage: false });
  await page.locator('.platform-center-sheet [data-action="closeModal"]').click();
  const requestItem = page.locator('.requests-disclosure[open] .request-item-disclosure').first();
  await requestItem.locator(':scope > summary').click();
  const requestStatusShape = await requestItem.locator('.request-status').first().evaluate(element => {
    const style = getComputedStyle(element);
    return { radius: parseFloat(style.borderRadius), width: element.getBoundingClientRect().width, height: element.getBoundingClientRect().height };
  });
  assert(requestStatusShape.radius <= 10 && requestStatusShape.width > requestStatusShape.height, `Request status is not a framed rectangular label: ${JSON.stringify(requestStatusShape)}`);
  const passportFits = await requestItem.locator('.request-compact-facts > span').evaluateAll(items => items.length === 3 && items.every(item => item.scrollWidth <= item.clientWidth + 1 && item.querySelector('b')?.scrollWidth <= item.querySelector('b')?.clientWidth + 1));
  assert(passportFits, 'Location, timing, or priority text is cramped inside the request summary.');
  await page.locator('.requests-disclosure summary').first().click();
  const repeatSource = await page.evaluate(() => {
    const key = 'KHADAMATI_PRIVATE_STATE_V1';
    const raw = sessionStorage.getItem(key);
    const state = JSON.parse(raw || '{}');
    const request = state.customerRequests?.[0];
    if (!request) return null;
    sessionStorage.setItem('KHADAMATI_REPEAT_TEST_BACKUP', raw);
    request.status = 'cancelled';
    request.offersOpen = false;
    request.date = '2026-08-18T15:30';
    request.dateOnly = '2026-08-18';
    request.idempotencyKey = 'original-request-key';
    sessionStorage.setItem(key, JSON.stringify(state));
    return {
      serviceValue: request.serviceValue,
      gov: request.gov,
      wilayah: request.wilayah,
      idempotencyKey: request.idempotencyKey,
    };
  });
  assert(repeatSource, 'Could not prepare the isolated repeat-request behavior check.');
  await page.reload({ waitUntil: 'domcontentloaded' });
  await clickUserNav(page, 'myAccount');
  const repeatButton = await revealRequestAction(page, 'repeatRequest');
  await repeatButton.click();
  assert(await page.locator('#qrEditId').inputValue() === '', 'Repeat request reused the old request identity.');
  assert(await page.locator('#qrService').inputValue() === repeatSource.serviceValue, 'Repeat request lost the selected service.');
  assert(await page.locator('#qrGov').inputValue() === repeatSource.gov && await page.locator('#qrWilayah').inputValue() === repeatSource.wilayah, 'Repeat request lost the selected area.');
  assert(await page.locator('#qrIdempotencyKey').inputValue() !== repeatSource.idempotencyKey, 'Repeat request reused the old idempotency key.');
  assert(await page.locator('#qrDate, #qrTime').count() === 0, 'Direct request should not ask for appointment timing before an offer is selected.');
  await page.locator('[data-action="closeModal"]').click();
  await page.evaluate(() => {
    const backup = sessionStorage.getItem('KHADAMATI_REPEAT_TEST_BACKUP');
    if (backup) sessionStorage.setItem('KHADAMATI_PRIVATE_STATE_V1', backup);
    sessionStorage.removeItem('KHADAMATI_REPEAT_TEST_BACKUP');
  });
  await page.reload({ waitUntil: 'domcontentloaded' });
  await clickUserNav(page, 'myAccount');
  assert(await page.locator('.loyalty-card-campaign [role="progressbar"]').count(), 'Clear loyalty progress bar is missing.');
  assert(await page.locator('.loyalty-card-campaign.is-points-only').count(), 'Loyalty must default to a points balance until management activates a campaign.');
  assert(!/المكافأة التالية|next reward/i.test(await page.locator('.loyalty-card-campaign').innerText()), 'Loyalty promises an unapproved fixed reward.');
  await page.locator('details.account-disclosure:has([data-action="openAppearance"]) > summary').click();
  await page.locator('[data-action="openAppearance"]').click();
  await page.locator('[data-action="setTheme"][data-value="dark"]').click();
  assert(await page.locator('body').getAttribute('data-theme') === 'dark', 'Dark theme was not applied immediately.');
  const darkPanelColor = await page.locator('.appearance-options').first().evaluate(element => getComputedStyle(element.closest('.modal')).backgroundColor);
  assert(!/rgb\(255, 255, 255\)/.test(darkPanelColor), 'Dark theme still renders a light appearance panel.');
  await page.locator('[data-action="setTheme"][data-value="light"]').click();
  assert(await page.locator('body').getAttribute('data-theme') === 'light', 'Light theme was not restored immediately.');
  await page.locator('[data-action="setDisplayScale"][data-value="large"]').click();
  assert(await page.locator('body').getAttribute('data-scale') === 'large', 'Large text mode was not applied.');
  await page.locator('[data-action="setDisplayScale"][data-value="normal"]').click();
  assert(await page.locator('body').getAttribute('data-scale') === 'normal', 'Default text mode was not restored after the accessibility check.');
  await page.locator('[data-action="closeModal"]').click();
  await clickUserNav(page, 'home');

  await page.locator('[data-action="goBack"]').click();
  await page.locator('[data-action="enterProvider"]').click();
  await capture(page, '00e-provider-entry', { fullPage: false });
  await page.locator('[data-action="openProviderAccess"][data-mode="register"]').click();
  await capture(page, '00g-provider-registration-basics', { fullPage: false });
  assert(await page.locator('#providerRegisterForm').count(), 'Register provider must open the registration form directly.');
  assert(await page.locator('#regCredentialExpiry').count(), 'Provider registration must collect the licence or registration expiry date.');
  assert(await page.locator('#regLegalPath').count(), 'Individual provider registration is missing the legal pathway selector.');
  assert(!(await page.locator('#regCredentialExpiry').getAttribute('required')), 'Omani individual registration incorrectly requires a commercial licence expiry.');
  await page.locator('#regLegalPath').selectOption('individual_foreign');
  assert(await page.locator('#regEmployerName').isVisible(), 'Non-Omani registration did not reveal employer details.');
  assert(await page.locator('#regWorkPermitExpiry').isVisible(), 'Non-Omani registration did not reveal work permit expiry.');
  await page.locator('#regLegalPath').selectOption('individual_omani');
  assert(!(await page.locator('#regEmployerName').isVisible()), 'Foreign-worker fields remained visible for an Omani individual.');
  assert(await page.locator('.provider-reg-progress [data-action="providerRegistrationStep"]').count() === 3, 'Provider registration must use three focused stages.');
  await page.locator('#regName').fill('مزود اختبار');
  await page.locator('#regPhone').fill('91234567');
  await page.locator('#regAge').fill('30');
  await page.locator('#regNationality').fill('عماني');
  await page.locator('#regPin').fill('2468');
  await page.locator('[data-action="providerRegistrationNext"]').click();
  assert(await page.locator('.provider-registration-flow[data-step="2"]').count(), 'Provider registration did not open its services stage.');
  assert(await page.locator('#providerRegisterForm .registration-subservice.show').count() === 0, 'Optional sub-services should start collapsed.');
  const progressDirection = await page.locator('.provider-reg-progress').evaluate(element => getComputedStyle(element).direction);
  assert(progressDirection === 'rtl', 'Arabic provider registration progress must run right to left.');
  assert(await page.locator('[data-action="addRegistrationSubservice"]').isVisible(), 'Individual registration must expose plan-limited additional services.');
  await page.locator('#regService').evaluate(select => {
    const option = [...select.options].find(item => item.value);
    select.value = option?.value || '';
    select.dispatchEvent(new Event('change', { bubbles: true }));
  });
  const individualLimit = Number((await page.locator('#regPlanGuidance').textContent()).match(/\d+/)?.[0] || 1);
  for (let index = 1; index < individualLimit; index += 1) await page.locator('[data-action="addRegistrationSubservice"]').click();
  assert(await page.locator('#providerRegisterForm .registration-subservice.show').count() === Math.max(0, individualLimit - 1), 'Individual registration did not follow the current foundation-plan service limit.');
  const individualCategory = String(await page.locator('#regService').inputValue()).split('|')[0];
  if (individualLimit > 1) assert(await page.locator('#regServiceExtra1').getAttribute('data-cat-filter') === individualCategory, 'Individual additional services must stay inside the primary category.');
  await page.locator('#regAvatar').setInputFiles(path.join(__dirname, '..', 'app-icon-512.png'));
  await page.waitForSelector('.image-editor-modern');
  assert(await page.locator('.image-editor-modern [data-action="cropZoomDelta"]').count() === 2, 'The modern image editor must provide zoom-in and zoom-out controls.');
  assert(await page.locator('.image-editor-modern [data-action="rotateCrop"]').count(), 'The modern image editor is missing rotation controls.');
  assert(Number(await page.locator('#cropZoom').getAttribute('min')) < 1, 'The image editor cannot zoom out enough to show a full portrait.');
  await capture(page, '01f-image-editor');
  await page.locator('.image-editor-modern [data-action="closeImageEditor"]').click();
  assert(await page.locator('.image-input-previews[data-for="regAvatar"] [data-action="editSelectedImage"]').count(), 'Uploaded image preview is missing its edit action.');
  assert(await page.locator('.image-input-previews[data-for="regAvatar"] [data-action="removeSelectedImage"]').count(), 'Uploaded image preview is missing its delete action.');
  await page.locator('[data-action="providerRegistrationBack"]').click();
  await page.locator('[data-action="setProviderRegisterType"][data-value="company"]').click();
  assert(await page.locator('#regCredentialExpiry').getAttribute('required') !== null, 'Company registration does not require the commercial registration expiry.');
  await page.locator('#regCompanyName').fill('شركة الاختبار');
  await page.locator('#regCommercialNo').fill('1234567');
  await page.locator('#regCredentialExpiry').fill('2028-12-31');
  await page.locator('[data-action="providerRegistrationNext"]').click();
  assert(await page.locator('[data-action="addRegistrationSubservice"]').isVisible(), 'Company registration must offer plan-limited services.');
  const companyLimit = Number((await page.locator('#regPlanGuidance').textContent()).match(/\d+/)?.[0] || 1);
  const companyRowsBefore = await page.locator('#providerRegisterForm .registration-subservice.show').count();
  await page.locator('[data-action="addRegistrationSubservice"]').click();
  const companyRowsAfter = await page.locator('#providerRegisterForm .registration-subservice.show').count();
  assert(companyRowsAfter === Math.min(companyRowsBefore + 1, Math.max(0, companyLimit - 1)), 'Company add-service must reveal one field at a time while respecting its current plan.');
  await capture(page, '01e-provider-register');
  await page.locator('#modalRoot [data-action="closeModal"]').click();
  await page.locator('[data-action="toggleLang"]').first().click();
  await page.locator('[data-action="openProviderAccess"][data-mode="register"]').click();
  while ((await page.locator('#providerRegisterForm').getAttribute('data-step')) !== '1') {
    await page.locator('[data-action="providerRegistrationBack"]').click();
  }
  const registrationHasArabic = async () => page.locator('#providerRegisterForm').evaluate(form => [...form.querySelectorAll('label,button,option,[placeholder],.account-type-note,.upload-hint,h3')].some(element => /[\u0600-\u06ff]/.test((element.getAttribute('placeholder') || element.textContent || '').trim())));
  assert(!(await registrationHasArabic()), 'English individual-provider registration still contains Arabic interface labels.');
  await page.locator('[data-action="setProviderRegisterType"][data-value="company"]').click();
  assert(!(await registrationHasArabic()), 'English company registration still contains Arabic interface labels.');
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), 'English provider registration overflows horizontally.');
  await page.locator('#modalRoot [data-action="closeModal"]').click();
  await page.locator('[data-action="toggleLang"]').first().click();
  await capture(page, '00f-provider-login', { fullPage: false });
  await page.locator('#loginPhone').fill('91234567');
  await page.locator('#loginOtp').fill('1234');
  await page.locator('[data-action="providerLogin"]').click();
  await page.waitForSelector('.role-onboarding');
  await page.locator('[data-action="skipOnboarding"]').click();
  const dualRoleAuth = await page.evaluate(() => JSON.parse(sessionStorage.getItem('KHADAMATI_AUTH_V3') || '{}'));
  assert(dualRoleAuth.providerToken === 'ui-provider-token' && dualRoleAuth.userToken === 'ui-user-token', 'Provider sign-in discarded the existing user session on the same device.');
  assert(dualRoleAuth.activeRole === 'provider', 'Provider sign-in did not activate the provider session context.');
  const accountMemory = await page.evaluate(() => JSON.parse(localStorage.getItem('KHADAMATI_ACCOUNT_MEMORY_V1') || '{}'));
  assert(accountMemory.user?.id === 'ui-user' && accountMemory.provider?.id === 'p1', 'The device did not retain both account identities for a later app launch.');
  const resumedPage = await context.newPage();
  await resumedPage.goto(testUrl, { waitUntil: 'domcontentloaded' });
  await resumedPage.waitForSelector('.provider-topbar');
  await dismissProviderSectionGuide(resumedPage);
  assert(await resumedPage.locator('[data-action="openProviderAccess"]').count() === 0, 'A full app reopen returned the saved provider to the sign-in gateway.');
  await resumedPage.locator('.provider-top-actions [data-action="switchAccountMode"]').click();
  await resumedPage.waitForSelector('.app-top');
  assert(await resumedPage.locator('#customerLoginPhone').count() === 0, 'Switching to the saved customer account requested credentials again.');
  await resumedPage.locator('.app-top [data-action="switchAccountMode"]').click();
  await resumedPage.waitForSelector('.provider-topbar');
  assert(await resumedPage.locator('#loginOtp').count() === 0, 'Switching back to the saved provider account requested the PIN again.');
  await resumedPage.close();
  await page.waitForTimeout(200);
  if (await page.locator('#modalRoot .modal-backdrop.show').count()) {
    assert(await page.locator('#modalRoot .notification-disclosure').count(), 'Provider login notification popup is empty.');
    await page.locator('#modalRoot [data-action="closeModal"]').first().click();
  }
  const providerBadge = page.locator('.provider-top-actions .notification-badge').first();
  if (await providerBadge.count()) {
    const badgeBox = await providerBadge.boundingBox();
    const bellBox = await providerBadge.locator('..').boundingBox();
    assert(badgeBox && bellBox && badgeBox.x >= bellBox.x - 10 && badgeBox.x <= bellBox.x + bellBox.width + 10, 'Provider notification badge is not anchored to the bell.');
  }
  assert(await page.locator('.week-calendar').count(), 'Provider weekly calendar is missing.');
  assert(await page.locator('.quote-template-grid').count(), 'Provider quote templates are missing.');
  assert(await page.locator('.provider-topbar .provider-brand').isVisible(), 'Provider header identity is hidden.');
  assert(await page.locator('.provider-topbar .provider-brand > .brand-mark.image-mark').isVisible(), 'Provider header logo is hidden on a narrow phone.');
  assert(await page.locator('.provider-topbar .provider-brand > span:last-child').isVisible(), 'Provider identity is hidden from the independent provider header.');
  const providerTopFits = await page.locator('.provider-topbar').evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1);
  assert(providerTopFits, 'Provider top bar overflows the mobile viewport.');
  assert(await page.locator('.provider-top-actions > *').evaluateAll(items => items.every(item => { const box = item.getBoundingClientRect(); return box.left >= -1 && box.right <= window.innerWidth + 1; })), 'A provider header control leaves the mobile viewport.');
  await capture(page, '02-provider-dashboard');
  await page.locator('.provider-top-actions [data-action="toggleLang"]').click();
  await page.waitForTimeout(150);
  assert(await page.locator('html').getAttribute('dir') === 'ltr', 'Provider English mode did not switch to LTR.');
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), 'Provider English layout overflows horizontally.');
  const providerNav = IS_MOBILE ? page.locator('.provider-bottom-nav') : page.locator('.provider-desktop-nav');
  const providerNavFits = await providerNav.evaluate(element => {
    const rect = element.getBoundingClientRect();
    return rect.left >= -1 && rect.right <= window.innerWidth + 1;
  });
  assert(providerNavFits, 'Provider navigation leaves the active viewport.');
  if (IS_MOBILE) {
    const providerNavLabels = await page.locator('.provider-bottom-nav-item').allTextContents();
    assert(providerNavLabels.length === 5, 'Provider phone navigation must contain exactly five destinations.');
    assert(providerNavLabels.every(label => label.trim()), 'A provider phone navigation destination has no accessible label.');
  }
  await page.locator('.provider-status-toggle').click();
  await page.locator('.provider-status-toggle').click();
  await page.locator('.provider-status-toggle').click();
  await page.locator('.provider-top-actions [data-action="openNotifications"]').click();
  await page.locator('[data-action="notificationCenterTab"][data-value="updates"]').click();
  assert(await page.locator('.notification-center-sheet').getByText(/Your card is visible to customers|Your card was temporarily paused/i).count(), 'Stored provider status notifications were not translated into English.');
  assert(await page.locator('.notification-center-sheet').getByText(/بطاقتك متاحة للعملاء|تم إيقاف بطاقتك مؤقتاً/i).count() === 0, 'Arabic system notification copy leaked into English mode.');
  await page.locator('[data-action="closeModal"]').click();
  await capture(page, '02a-provider-english');
  await page.locator('.provider-top-actions [data-action="toggleLang"]').click();
  await page.waitForTimeout(100);
  assert(await page.locator('html').getAttribute('dir') === 'rtl', 'Provider Arabic mode was not restored to RTL.');
  await clickFirstAction(page, 'openQuoteLibrary');
  assert(await page.locator('.modal-title').filter({ hasText: /عرض السعر|price and duration/i }).count(), 'Quote template sheet did not open.');
  await page.locator('[data-action="closeModal"]').click();
  await clickFirstAction(page, 'manageQuoteTemplates');
  assert(await page.locator('.quote-manager-list article').count() >= 1, 'Editable provider quote templates are missing.');
  await page.locator('[data-action="editQuoteTemplate"]').first().click();
  await page.locator('#quoteEditAr').fill('عرض فحص مخصص');
  await page.locator('#quoteEditEn').fill('Custom inspection offer');
  await page.locator('[data-action="saveQuoteTemplate"]').click();
  await page.waitForFunction(() => document.querySelector('.quote-manager-list')?.textContent?.includes('عرض فحص مخصص'));
  assert(await page.locator('.quote-manager-list').getByText('عرض فحص مخصص').count(), 'Provider quote template edit was not retained.');
  await page.locator('[data-action="closeModal"]').click();

  await clickProviderNav(page, 'business');
  await page.waitForSelector('.platform-dashboard-grid');
  await dismissProviderSectionGuide(page, '02c-provider-business-guide');
  assert(await page.locator('.legal-profile-card').count(), 'Provider legal pathway summary is missing from the business center.');
  assert(await page.locator('.platform-workspace-section').count() >= 3, 'Provider CRM, contracts, or training workspace is incomplete.');
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), 'Provider business center overflows the mobile viewport.');
  await capture(page, '02c-provider-business');
  await clickProviderNav(page, 'home');
  await dismissProviderSectionGuide(page, '02d-provider-today-guide');

  await clickProviderNav(page, 'leads');
  await dismissProviderSectionGuide(page, '02e-provider-community-guide');
  await page.locator('.community-v3-tabs [data-value="packages"]').click();
  assert(await page.locator('.community-page[data-community-mode="provider"]').count(), 'Provider Community destination is missing.');
  await page.locator('#toastRoot').evaluate(element => { element.innerHTML = ''; });
  await capture(page, '08e-community-provider-packages', { fullPage: false });
  await page.locator('.community-fab').click();
  assert(await page.locator('.community-editor').count(), 'Provider package editor did not open.');
  await capture(page, '08f-community-package-editor', { fullPage: false });
  await page.locator('.community-editor [data-action="closeModal"]').click();
  await page.locator('.community-v3-tabs [data-value="board"]').click();
  assert(await page.locator('.community-need-card').count(), 'Provider request board is empty.');
  assert(await page.locator('.community-need-card [data-action="providerAcceptRequest"]').count(), 'Matching provider cannot offer from the request board.');
  await capture(page, '02b-provider-opportunities');
  await page.locator('[data-action="providerAcceptRequest"]').first().click();
  await page.locator('#offerPrice').fill('12');
  await page.locator('#offerDuration').fill('خلال ساعتين');
  await page.locator('#offerNote').fill('يشمل المعاينة والتنفيذ');
  await page.locator('[data-action="submitProviderOffer"]').click();
  await page.waitForSelector('#modalRoot .modal-backdrop', { state: 'detached' });
  await page.locator('.provider-top-actions [data-action="switchAccountMode"]').click();
  const customerModeAuth = await page.evaluate(() => JSON.parse(sessionStorage.getItem('KHADAMATI_AUTH_V3') || '{}'));
  assert(customerModeAuth.activeRole === 'user' && customerModeAuth.userToken === 'ui-user-token', 'Returning from provider mode did not restore the customer session.');
  await clickUserNav(page, 'myAccount');
  assert(await page.locator('.requests-disclosure [data-action="compareRequestOffers"]').count(), 'Offer comparison action is missing from the current request summary.');
  await page.waitForTimeout(600);
  if (await page.locator('#modalRoot .modal-backdrop.show').count()) {
    assert(await page.locator('#modalRoot .notification-disclosure').count(), 'Unexpected modal blocked offer comparison.');
    await page.locator('#modalRoot [data-action="closeModal"]').first().click();
  }
  const compareOffers = await revealRequestAction(page, 'compareRequestOffers');
  await compareOffers.click();
  assert(await page.locator('.offer-card').count(), 'Offer comparison card is missing.');
  await capture(page, '04-offer-comparison');
  await page.locator('[data-action="acceptRequestOffer"]').first().click();

  await page.waitForSelector('.chat-sheet #chatThread');
  assert(await page.locator('.chat-profile-identity b').filter({ hasText: /سالم البلوشي/i }).count(), 'Accepted offer did not open the selected provider chat.');
  assert(await page.locator('.chat-message.theirs').filter({ hasText: /شكراً لاختيار عرضي|Thank you for choosing my offer/i }).count(), 'Automatic provider welcome message is missing.');
  await page.locator('[data-action="closeModal"]').click();

  const requestDetails = await revealRequestAction(page, 'openRequestTaskDetails');
  await requestDetails.click();
  await page.waitForSelector('.task-detail-sheet');
  assert(await page.locator('.request-offer-summary').count() === 0, 'Offer comparison remained visible after selecting a provider.');
  assert(await page.locator('[data-action="compareRequestOffers"]').count() === 0, 'Compare action remained available after offer selection.');
  assert(await page.locator('[data-action="manageRequestContact"]').count(), 'Contact privacy control is missing after provider selection.');
  assert(await page.locator('[data-action="openRequestChat"]').count(), 'In-app chat did not remain available after provider selection.');
  assert(await page.locator('[data-action="requestWhatsapp"]').count() === 0, 'WhatsApp must stay hidden before customer consent.');
  assert(await page.locator('[data-action="requestCall"]').count() === 0, 'Phone calls must stay hidden before customer consent.');
  assert(await page.locator('[data-action="startCustomerRequest"]').count() === 0, 'Work can start before both parties confirm the agreement.');
  assert(await page.locator('[data-action="editRequestAgreement"]').count(), 'Accepted request does not guide the customer to confirm the agreement.');
  const contactControl = await revealRequestAction(page, 'manageRequestContact');
  await contactControl.click();
  assert(await page.locator('#contactAllowChat').isChecked(), 'In-app chat consent was not enabled by offer selection.');
  await page.locator('#contactAllowWhatsapp').check();
  await page.locator('#contactAllowCall').check();
  await page.locator('[data-action="saveRequestContactConsent"]').click();
  await page.waitForSelector('.contact-consent-sheet', { state: 'detached' });
  const detailsAfterConsent = await revealRequestAction(page, 'openRequestTaskDetails');
  await detailsAfterConsent.click();
  await page.waitForSelector('.task-detail-sheet');
  await revealRequestAction(page, 'openRequestChat');
  assert(await page.locator('[data-action="requestWhatsapp"]').count(), 'WhatsApp was not enabled after customer consent.');
  assert(await page.locator('[data-action="requestCall"]').count(), 'Phone calls were not enabled after customer consent.');
  const chatAction = await revealRequestAction(page, 'openRequestChat');
  await chatAction.click();
  const chatViewportFit = await page.locator('.chat-sheet').evaluate((sheet, isMobile) => {
    const rect = sheet.getBoundingClientRect();
    const composer = sheet.querySelector('.chat-composer')?.getBoundingClientRect();
    const composerFits = composer
      && composer.left >= rect.left - 1
      && composer.right <= rect.right + 1
      && composer.bottom <= Math.min(rect.bottom, innerHeight) + 1;
    if (isMobile) {
      return { ok: rect.top <= 1 && rect.left <= 1 && rect.right >= innerWidth - 1 && rect.bottom >= innerHeight - 1 && composerFits, rect: {...rect.toJSON()}, composer: composer ? {...composer.toJSON()} : null };
    }
    const desktopSize = rect.width >= Math.min(innerWidth - 40, 680)
      && rect.height >= Math.min(innerHeight - 40, 620);
    const desktopBounds = rect.top >= -1 && rect.left >= -1 && rect.right <= innerWidth + 1 && rect.bottom <= innerHeight + 1;
    return { ok: desktopSize && desktopBounds && composerFits, rect: {...rect.toJSON()}, composer: composer ? {...composer.toJSON()} : null };
  }, IS_MOBILE);
  assert(chatViewportFit.ok, `Chat does not fit the active viewport or its composer overflows the conversation window: ${JSON.stringify(chatViewportFit)}`);
  assert(await page.locator('.chat-sheet [data-action="refreshRequestChat"]').count() === 0, 'Chat still exposes a manual refresh button.');
  assert(await page.evaluate(() => Boolean(window.__khadamatiChatPoll)), 'Chat automatic refresh did not start.');
  await page.locator('.chat-sheet [data-action="manageRequestContact"]').click();
  assert(await page.locator('.contact-consent-sheet').count(), 'Contact choices did not open above the active chat.');
  await page.locator('.contact-consent-sheet [data-action="closeModalSoft"]').click();
  assert(await page.locator('.chat-sheet #chatThread').count(), 'Closing contact choices did not restore the same chat.');
  await page.locator('.chat-sheet [data-action="manageRequestContact"]').click();
  await page.locator('.contact-consent-sheet [data-action="saveRequestContactConsent"]').click();
  await page.waitForSelector('.chat-sheet #chatThread');
  assert(await page.locator('.chat-sheet #chatThread').count(), 'Saving contact choices removed the user from the active chat.');
  assert(await page.locator('.chat-quick-replies button').count() >= 4, 'Chat quick replies or location sharing are missing.');
  assert(await page.locator('[data-action="shareChatLocation"]').count() === 1, 'Chat location sharing action is missing.');
  await page.locator('[data-action="shareChatLocation"]').click();
  await page.waitForSelector('.chat-message.mine .chat-location-card');
  assert((await page.locator('.chat-location-card').first().getAttribute('href')).includes('google.com/maps'), 'Shared chat location does not open in a maps application.');
  await page.locator('.chat-quick-replies [data-action="chatQuickReply"]').first().click();
  assert(Boolean(await page.locator('#chatText').inputValue()), 'Quick reply did not fill the chat composer.');
  await page.locator('#chatText').fill('تم تأكيد الموعد');
  await page.locator('[data-action="sendChatMessage"]').click();
  await page.waitForSelector('.chat-message.mine');
  const chatImagePath = path.join(__dirname, '..', 'app-icon-192.png');
  await page.locator('#chatImage').setInputFiles({
    name: 'chat-image.png',
    mimeType: 'image/png',
    buffer: fs.readFileSync(chatImagePath),
  });
  await page.waitForSelector('.chat-image-preview-sheet');
  assert(await page.locator('.chat-image-preview-sheet img').count() === 1, 'Selected chat image has no preview.');
  assert(await page.locator('.chat-message.mine img').count() === 0, 'Chat image was sent before confirmation.');
  await page.locator('#chatImageCaption').fill('صورة توضيحية');
  await page.locator('[data-action="confirmChatImage"]').click();
  await page.waitForFunction(() => Boolean(document.querySelector('.chat-message.mine img')), null, { timeout: 15000 }).catch(() => {});
  const imageMessageState = await page.evaluate(() => ({
    imageCount: document.querySelectorAll('.chat-message.mine img').length,
    mineCount: document.querySelectorAll('.chat-message.mine').length,
    composerText: document.querySelector('#chatText')?.value || '',
    selectedFiles: document.querySelector('#chatImage')?.files?.length || 0,
    sendBusy: document.querySelector('[data-action="sendChatMessage"]')?.dataset.busy || '',
    toast: document.querySelector('#toastRoot .toast')?.textContent?.trim() || '',
  }));
  assert(imageMessageState.imageCount > 0, `Chat image was not sent: ${JSON.stringify(imageMessageState)}`);
  await page.locator('[data-action="toggleChatRecording"]').click();
  await page.waitForTimeout(900);
  await page.locator('[data-action="toggleChatRecording"]').click();
  await page.waitForSelector('.voice-ready:not(:empty)');
  assert(await page.locator('[data-action="cancelChatAudio"]').count() === 1, 'Voice recording cannot be discarded before sending.');
  await page.locator('[data-action="cancelChatAudio"]').click();
  assert(await page.locator('.voice-ready:not(:empty)').count() === 0, 'Discarded voice recording remained in the composer.');
  await page.locator('[data-action="toggleChatRecording"]').click();
  await page.waitForTimeout(900);
  await page.locator('[data-action="toggleChatRecording"]').click();
  await page.waitForSelector('.voice-ready:not(:empty)');
  assert(await page.locator('[data-action="reRecordChatAudio"]').count() === 1, 'Voice preview cannot be re-recorded.');
  await page.locator('[data-action="sendChatAudio"]').click();
  await page.waitForSelector('.chat-message.mine .voice-note-player');
  await capture(page, '05-request-chat', { fullPage: false });
  await page.locator('[data-action="closeModal"]').click();
  assert(await page.evaluate(() => !window.__khadamatiChatPoll), 'Chat automatic refresh continued after closing the conversation.');

  const originalBookingRequests = await page.evaluate(() => JSON.parse(sessionStorage.getItem('KHADAMATI_PRIVATE_STATE_V1') || '{}').customerRequests || []);
  const bookingV2Fixture = JSON.parse(JSON.stringify(originalBookingRequests));
  const bookingV2Request = bookingV2Fixture.find(item => item.acceptedProviderId);
  assert(bookingV2Request, 'Could not prepare the isolated booking_v2 request check.');
  {
    const request = bookingV2Request;
    request.workflowVersion = 'booking_v2';
    request.fulfillmentMode = 'quoted';
    request.status = 'accepted';
    request.visibleState = 'booked';
    request.allowedActions = ['open_chat', 'request_change'];
    request.nextAction = { type: 'review_change_order', label: 'راجع التعديل', enabled: true, changeOrderId: 'ui-change-order', expectedVersion: 1 };
    request.workOrderSummary = {
      version: 1, priceAmount: 12, currency: 'OMR', appointmentAt: '2026-08-18T11:30:00Z',
      durationMinutes: 90, warrantyDays: 14, scope: 'فحص اللوحة وتنفيذ الإصلاح المتفق عليه',
      exclusions: 'قطع إضافية غير مدرجة', evidencePolicy: 'optional', startVerificationMode: 'none',
    };
    request.changeOrders = [{
      id: 'ui-change-order', status: 'pending', proposedByKind: 'provider', expectedVersion: 1,
      reason: 'تعديل الموعد بعد تنسيق الوصول', changes: { appointmentAt: '2026-08-19T12:30:00Z', priceAmount: 14 },
    }];
  }
  mockCustomerRequests = bookingV2Fixture;
  mockBookingV2Enabled = true;
  const bookingV2Bootstrap = page.waitForResponse(response => new URL(response.url()).pathname === '/api/bootstrap');
  await page.reload({ waitUntil: 'domcontentloaded' });
  await bookingV2Bootstrap;
  await page.waitForTimeout(100);
  await clickUserNav(page, 'tasks');
  const bookingV2Details = await revealRequestAction(page, 'openRequestTaskDetails');
  await bookingV2Details.click();
  await page.waitForSelector('.task-detail-sheet .work-order-summary');
  assert(await page.locator('.task-detail-sheet .request-agreement-card').count() === 0, 'booking_v2 still renders a duplicate execution agreement.');
  assert(await page.locator('.task-detail-sheet [data-action="startCustomerRequest"], .task-detail-sheet [data-action="workflowStartRequest"]').count() === 0, 'Customer can start booking_v2 work from the UI.');
  assert(await page.locator('.task-detail-sheet .task-next-action').count() === 1, 'booking_v2 does not expose exactly one primary next action.');
  assert(await page.locator('.work-order-summary').getByText(/فحص اللوحة وتنفيذ الإصلاح/).count(), 'Accepted work-order scope is missing.');
  await page.locator('.work-order-change.pending').click();
  assert(await page.locator('.change-order-sheet .change-compare-row').count() === 2, 'Pending change order is not compared field by field.');
  assert(await page.locator('.change-order-sheet').getByText('تعديل الموعد بعد تنسيق الوصول').count(), 'Change-order reason is missing.');
  assert(await page.locator('.change-order-sheet [data-action="decideChangeOrder"]').count() === 2, 'Customer cannot approve or reject the provider change order.');
  await page.locator('.change-order-sheet [data-action="closeModal"]').click();
  mockCustomerRequests = originalBookingRequests;
  mockBookingV2Enabled = null;
  const legacyRestoreBootstrap = page.waitForResponse(response => new URL(response.url()).pathname === '/api/bootstrap');
  await page.reload({ waitUntil: 'domcontentloaded' });
  await legacyRestoreBootstrap;
  await page.waitForTimeout(100);
  mockCustomerRequests = null;

  await page.evaluate(() => {
    const key = 'KHADAMATI_PRIVATE_STATE_V1';
    const state = JSON.parse(sessionStorage.getItem(key) || '{}');
    const request = state.customerRequests?.find(item => item.acceptedProviderId);
    if (!request) throw new Error('Accepted request is missing before agreement check.');
    delete request.workflowVersion;
    delete request.workflow_version;
    delete request.workOrderSummary;
    delete request.work_order_summary;
    delete request.nextAction;
    delete request.allowedActions;
    delete request.visibleState;
    delete request.changeOrders;
    request.fulfillmentMode = 'quoted';
    request.status = 'appointmentConfirmed';
    request.agreement = {
      version: 1,
      status: 'confirmed',
      appointmentAt: '2026-08-18T15:30',
      durationMinutes: 90,
      priceAmount: 12,
      locationText: `${request.gov || ''}، ${request.wilayah || ''}`,
      userConfirmed: true,
      providerConfirmed: true,
      updatedAt: new Date().toISOString(),
    };
    sessionStorage.setItem(key, JSON.stringify(state));
  });
  await page.reload({ waitUntil: 'domcontentloaded' });
  await clickUserNav(page, 'tasks');
  assert(await page.locator('[data-action="startCustomerRequest"], [data-action="workflowStartRequest"]').count() === 0, 'Customer must never receive the provider start-work action.');

  await revealRequestAction(page, 'openRequestTaskDetails').then(action => action.click());
  await page.locator('.task-detail-sheet').waitFor({ state: 'visible' });
  const calendarAction = await revealRequestAction(page, 'addRequestCalendar');
  const downloadPromise = page.waitForEvent('download');
  await calendarAction.click();
  const calendarDownload = await downloadPromise;
  assert((await calendarDownload.suggestedFilename()).endsWith('.ics'), 'Calendar export is not an ICS file.');
  await page.locator('.task-detail-sheet [data-action="closeModal"]').click();

  // The provider completion action is valid only after the server has advanced the job to in-progress.
  mockCustomerRequests = await page.evaluate(() => {
    const key = 'KHADAMATI_PRIVATE_STATE_V1';
    const state = JSON.parse(sessionStorage.getItem(key) || '{}');
    const request = state.customerRequests?.find(item => item.acceptedProviderId);
    if (!request) throw new Error('Accepted request is missing before provider in-progress check.');
    request.status = 'inProgress';
    request.visibleState = 'in_progress';
    request.updatedAt = new Date(Date.now() + 1000).toISOString();
    sessionStorage.setItem(key, JSON.stringify(state));
    return state.customerRequests;
  });

  await clickUserNav(page, 'myAccount');
  await page.locator('details.account-disclosure:has([data-action="providerMode"]) > summary').click();
  await page.locator('.account-menu [data-action="providerMode"], .account-menu [data-action="nav"][data-view="provider"]').first().click();
  await page.locator('.provider-top-actions [data-action="openNotifications"]').click();
  assert(await page.locator('.notification-center-tab').count() === 2, 'Notification center must keep only requests and updates.');
  assert(await page.locator('[data-action="notificationCenterTab"][data-value="messages"]').count() === 0, 'Chats still appear inside the notification center.');
  await page.locator('.notification-center-sheet [data-action="closeModal"]').click();
  await page.locator('.provider-top-actions [data-action="openConversations"]').click();
  await page.waitForSelector('.provider-workspace .conversation-page-list .conversation-card');
  const providerConversation = page.locator('.provider-workspace .conversation-page-list .conversation-card').first();
  assert(await providerConversation.count(), 'Provider conversation page is empty.');
  assert((await providerConversation.textContent()).includes('مستخدم الاختبار الآلي'), 'Provider conversation does not identify the customer.');
  await providerConversation.click();
  await page.waitForSelector('.chat-sheet #chatThread');
  assert(await page.locator('.chat-profile-identity b').filter({ hasText: /مستخدم الاختبار/i }).count(), 'Provider conversation page did not open the correct chat directly.');
  await page.locator('.chat-sheet [data-action="closeModalSoft"]').click();
  assert(await page.locator('.provider-workspace .conversation-page-list').count(), 'Closing a chat did not return to the provider conversation page.');
  await clickProviderNav(page, 'tasks');
  await dismissProviderSectionGuide(page, '06a-provider-tasks-guide');
  assert(await page.locator('.provider-active-jobs .provider-task-card').count(), 'Accepted request is missing from provider active jobs.');
  assert(await page.locator('.provider-active-jobs [data-action="providerAcceptRequest"]').count() === 0, 'Provider can still submit an offer after being selected.');
  const completionActionCount = await page.locator('.provider-active-jobs [data-action="openCompletionEvidence"]').count();
  assert(completionActionCount === 1, `Provider active job must expose exactly one completion action (found ${completionActionCount}).`);
  mockCustomerRequests = null;
  const providerChatAction = page.locator('.provider-active-jobs [data-action="openRequestChat"]').first();
  const providerTaskDetails = providerChatAction.locator('xpath=ancestor::details[contains(@class,"task-secondary-details")][1]');
  if (!await providerChatAction.isVisible() && await providerTaskDetails.count()) await providerTaskDetails.locator(':scope > summary').click();
  await providerChatAction.click();
  assert(await page.locator('[data-action="providerCustomerWhatsapp"]').count(), 'Selected provider cannot use customer-approved WhatsApp.');
  assert(await page.locator('[data-action="providerCustomerCall"]').count(), 'Selected provider cannot use customer-approved calls.');
  await page.locator('#chatText').fill('رسالة متابعة من سالم البلوشي');
  await page.locator('[data-action="sendChatMessage"]').click();
  await page.waitForSelector('.chat-message.mine');
  await page.locator('[data-action="closeModal"]').click();
  assert(await page.locator('[data-action="openArrivalTracking"]').count() === 0, 'Removed provider-arrival tracking is still exposed in active jobs.');
  assert(await page.locator('[data-action="updateProviderArrival"]').count() === 0, 'Removed provider-arrival controls are still exposed.');
  await capture(page, '06-provider-active-jobs');

  await clickProviderNav(page, 'profile');
  await dismissProviderSectionGuide(page, '06b-provider-account-guide');
  assert(await page.locator('.provider-space-title h1').filter({ hasText: /مساحتك|Your space/i }).count(), 'Provider account did not open the structured Your space page.');
  await page.locator('[data-action="openProviderProfileEditor"]').click();
  await page.waitForSelector('.provider-profile-edit-sheet');
  assert(await page.locator('#ppEmail').count(), 'Provider profile editor is missing email.');
  assert(await page.locator('#ppAge').count(), 'Individual provider profile editor is missing age.');
  assert(await page.locator('#ppNationality').count(), 'Individual provider profile editor is missing nationality.');
  await page.locator('#ppBeforeImage').setInputFiles(path.join(__dirname, '..', 'app-icon-192.png'));
  await page.locator('#ppAfterImage').setInputFiles(path.join(__dirname, '..', 'app-icon-512.png'));
  await page.locator('#ppBeforeAfterCaption').fill('نتيجة اختبار قبل وبعد');
  await page.locator('[data-action="saveBeforeAfterPair"]').click();
  await page.waitForSelector('.rich-media-editor .list-item');
  await page.locator('#ppIntroVideoUrl').fill('https://example.com/khadamati-intro.mp4');
  await page.locator('[data-action="saveProviderIntroVideo"]').click();
  await page.waitForSelector('.provider-media-preview');
  await page.locator('#ppEmail').fill('provider@example.test');
  await page.locator('#ppAge').fill('31');
  await page.locator('#ppNationality').fill('عُماني');
  await page.locator('#ppCommercialNo').fill('UI-LIC-100');
  await page.locator('#ppDocs').setInputFiles(path.join(__dirname, '..', 'app-icon-192.png'));
  await page.locator('[data-action="saveProviderProfile"]').click();
  await page.waitForSelector('.provider-profile-edit-sheet', { state: 'detached' });
  assert(await page.locator('.provider-space').count(), 'Saving an individual provider profile did not return to Your space.');
  await capture(page, '07-provider-media');

  await page.locator('.provider-top-actions [data-action="switchAccountMode"]').click();
  await clickUserNav(page, 'conversations');
  await page.waitForSelector('.conversation-page-list .conversation-card');
  const userConversation = page.locator('.conversation-page-list .conversation-card').first();
  assert((await userConversation.textContent()).includes('سالم البلوشي'), 'User conversation does not identify the provider.');
  await userConversation.click();
  await page.waitForSelector('.chat-sheet #chatThread');
  assert(await page.locator('.chat-profile-identity b').filter({ hasText: /سالم البلوشي/i }).count(), 'User conversation page did not open the correct chat directly.');
  await page.locator('.chat-sheet [data-action="closeModalSoft"]').click();
  assert(await page.locator('.conversation-page-list .conversation-card').count(), 'Closing the chat did not restore the user conversation page.');
  await clickUserNav(page, 'search');
  await page.waitForTimeout(600);
  if (await page.locator('#modalRoot .modal-backdrop.show').count()) {
    assert(await page.locator('#modalRoot .notification-disclosure').count(), 'Unexpected modal blocked public provider profile.');
    await page.locator('#modalRoot [data-action="closeModal"]').first().click();
  }
  await page.locator('[data-action="providerDetails"][data-id="p1"]').first().click();
  assert(await page.locator('.provider-intro-video').count(), 'Provider introduction video is missing from the public profile.');
  assert(await page.locator('.before-after-card').count(), 'Before/after gallery is missing from the public profile.');
  assert(await page.locator('.provider-detail-sheet [data-action="openWorkImage"]').count() === 2, 'Provider work photos are not openable controls.');
  const workDisclosure = page.locator('.provider-detail-disclosure').filter({ has: page.locator('[data-action="openWorkImage"]') });
  if (!(await workDisclosure.getAttribute('open'))) await workDisclosure.locator('summary').click();
  await page.locator('.provider-detail-sheet [data-action="openWorkImage"]').first().click();
  await page.waitForSelector('.media-viewer');
  assert(await page.locator('.media-viewer-stage img').isVisible(), 'The full work-image viewer did not show the selected photo.');
  await page.locator('[data-action="closeMediaViewer"]').click();
  assert(await page.locator('.provider-detail-sheet').count(), 'Closing a work photo did not restore the same provider profile.');
  await page.locator('.provider-detail-sheet [data-action="openProviderOnMap"]').click();
  await page.waitForSelector('.live-map-full .map-my-location');
  await page.locator('.live-map-full .map-my-location').click();
  await page.waitForTimeout(250);
  await page.locator('.live-map-full [data-action="closeModalSoft"]').click();
  assert(await page.locator('.provider-detail-sheet').count(), 'Closing the provider map must restore the same provider profile.');
  await page.locator('[data-action="closeModal"]').click();
  await clickUserNav(page, 'myAccount');
  await page.locator('details.account-disclosure:has([data-action="providerMode"]) > summary').click();
  await page.locator('.account-menu [data-action="providerMode"], .account-menu [data-action="nav"][data-view="provider"]').first().click();
  await page.waitForSelector(IS_MOBILE ? '.provider-bottom-nav' : '.provider-desktop-nav');
  await clickProviderNav(page, 'support');
  await dismissProviderSectionGuide(page);
  const accountSecurity = page.locator('.compact-settings-disclosure').filter({ has: page.locator('[data-action="confirmLogout"]') });
  if (!(await accountSecurity.getAttribute('open'))) await accountSecurity.locator('summary').click();
  await accountSecurity.locator('[data-action="confirmLogout"]').click();
  assert(await page.locator('.confirmation-sheet').count(), 'Provider sign-out confirmation is missing.');
  await page.locator('.confirmation-sheet [data-action="providerLogout"]').click();
  await page.waitForFunction(() => {
    const auth = JSON.parse(sessionStorage.getItem('KHADAMATI_AUTH_V3') || '{}');
    return auth.userToken === 'ui-user-token' && !auth.providerToken;
  });
  const authAfterProviderLogout = await page.evaluate(() => JSON.parse(sessionStorage.getItem('KHADAMATI_AUTH_V3') || '{}'));
  assert(authAfterProviderLogout.userToken === 'ui-user-token' && !authAfterProviderLogout.providerToken, 'Provider sign-out did not preserve the independent customer session.');
  await clickUserNav(page, 'myAccount');
  const userLogout = page.locator('[data-action="confirmLogout"][data-kind="user"]').first();
  const userSecurity = userLogout.locator('xpath=ancestor::details[1]');
  if (!await userLogout.isVisible() && await userSecurity.count()) await userSecurity.locator(':scope > summary').click();
  await userLogout.click();
  await page.locator('.confirmation-sheet [data-action="customerLogout"]').click();
  await page.waitForSelector('[data-action="enterGuest"]');
  await page.locator('[data-action="enterGuest"]').click();
  if (await page.locator('.role-onboarding').count()) {
    assert(/assets\/onboarding\/core\/guest-browse\.webp/.test(await page.locator('.role-onboarding .onboarding-visual img').getAttribute('src')), 'Guest onboarding did not open its dedicated artwork.');
    await page.locator('[data-action="skipOnboarding"]').click();
  }
  assert(await page.locator('.app-top [data-action="openNotifications"] .notification-badge').count() === 0, 'Guest must not inherit the previous user notification badge.');
  await page.locator('.app-top [data-action="openNotifications"]').click();
  assert(await page.locator('.notification-center-sheet .guest-note').count(), 'Guest notification privacy note is missing.');
  assert(await page.locator('.notification-center-sheet .notification-disclosure').count() === 0, 'Guest can see notifications from the previous signed-in account.');
  await page.locator('[data-action="closeModal"]').click();
  await page.locator('[data-action="goBack"]').click();
  for (let i = 0; i < 6; i++) await page.locator('[data-action="brandHome"]').first().click();
  if (await page.locator('[data-action="useAdminPassword"]').count()) {
    await page.locator('[data-action="useAdminPassword"]').click();
  }
  await page.waitForSelector('#adminCode');
  await page.locator('#adminCode').fill('UI-Test-4829');
  await page.locator('[data-action="adminLogin"]').click();
  await page.waitForSelector('.admin-shell');
  await page.locator('.admin-topbar [data-action="openAdminNotifications"]').click();
  assert(await page.locator('.admin-workspace .admin-notification-list').count(), 'The management bell did not open the independent administration notification center.');
  assert(await page.locator('.notification-center-sheet').count() === 0, 'The management bell incorrectly opened the customer notification sheet.');
  assert(await page.locator('.admin-mark-all').isVisible(), 'The mark-all action is hidden in the administration notification center.');
  const adminNotificationFit = await page.locator('.admin-mark-all').evaluate(element => {
    const box = element.getBoundingClientRect();
    return box.left >= 0 && box.right <= window.innerWidth && box.width > 0;
  });
  assert(adminNotificationFit, 'The administration notification action leaves the mobile viewport.');
  assert(await page.locator('.admin-notification-list .notification-disclosure').count(), 'Administration notifications are missing.');
  const firstAdminNotification = page.locator('.admin-notification-list .notification-disclosure').first();
  await firstAdminNotification.locator('summary').click();
  const adminNotificationActionsFit = await firstAdminNotification.locator('.notification-actions').evaluate(element => {
    const box = element.getBoundingClientRect();
    const parent = element.closest('.notification-disclosure').getBoundingClientRect();
    return box.left >= parent.left - 1 && box.right <= parent.right + 1 && box.width > 0;
  });
  assert(adminNotificationActionsFit, 'Expanded administration notification actions are clipped or hidden.');
  const adminDeleteAction = await firstAdminNotification.locator('.notification-delete-action').evaluate(element => {
    const style = getComputedStyle(element);
    const box = element.getBoundingClientRect();
    return { visible: box.width >= 32 && box.height >= 32 && style.display !== 'none' && style.visibility !== 'hidden', icon: Boolean(element.querySelector('svg')) };
  });
  assert(adminDeleteAction.visible && adminDeleteAction.icon, 'Administration notification delete action is visually blank.');
  await capture(page, '03a-admin-notifications');
  await clickAdminTab(page, 'subscriptions');
  assert(await page.locator('.subscription-command').count(), 'Subscription control center is missing.');
  assert(await page.locator('.package-admin-grid .package-admin-card').count() === 8, 'The production plan catalog must contain four individual and four company plans.');
  assert(await page.locator('.admin-plan-scope').count() === 2, 'Individual and company plans are not separated in administration.');
  assert(await page.locator('.admin-plan-scope').first().locator('.package-admin-card').count() === 4, 'Individual plans are incomplete.');
  assert(await page.locator('.admin-plan-scope').nth(1).locator('.package-admin-card').count() === 4, 'Company plans are incomplete.');
  await page.locator('.package-admin-grid [data-action="packageForm"]').first().click();
  await page.waitForSelector('#pkgMaxWilayats');
  assert(await page.locator('#pkgMaxServices').count(), 'Service limit is missing from plan management.');
  assert(await page.locator('#pkgMaxCategories').count(), 'Category limit is missing from plan management.');
  assert(await page.locator('#pkgMaxImages').count(), 'Image limit is missing from plan management.');
  assert(await page.locator('#pkgMaxGovernorates').count(), 'Governorate limit is missing from plan management.');
  assert(await page.locator('#pkgLeadDelaySeconds').count(), 'Plan lead-delay entitlement is missing.');
  assert(await page.locator('#pkgCommunityQuota').count(), 'Community listing quota is missing.');
  assert(await page.locator('#pkgTeamMembers').count(), 'Company team limit is missing.');
  assert(await page.locator('#pkgSharedInbox').count(), 'Plan shared-inbox entitlement is missing.');
  await page.locator('[data-action="closeModal"]').click();
  await clickAdminTab(page, 'finance');
  assert(await page.locator('.finance-command-grid').count(), 'Financial control center is missing.');
  await clickAdminTab(page, 'community');
  assert(await page.locator('.admin-community').count(), 'Community management center is missing.');
  await page.locator('[data-action="adminCommunityView"][data-value="board"]').click();
  assert(await page.locator('.admin-market-health').count(), 'Administration is missing request-board health indicators.');
  if (await page.locator('.admin-request-card').count()) {
    assert(await page.locator('.admin-request-card [data-action="adminOpenCustomerRequest"]').count(), 'Administration is missing its request operations action.');
    assert(await page.locator('.admin-request-card [data-action="adminCustomerRequestAction"]').count(), 'Administration cannot pause or resume a request independently.');
  }
  await page.locator('[data-action="adminCommunityView"][data-value="settings"]').click();
  assert(await page.locator('#communityEnabledSetting').count(), 'Community activation control is missing.');
  assert(await page.locator('#communityPackagesEnabledSetting').count(), 'Independent package activation control is missing.');
  assert(await page.locator('#communityBoardEnabledSetting').count(), 'Independent request-board activation control is missing.');
  assert(await page.locator('#communityProviderOffersSetting').count(), 'Provider-quote control is missing.');
  assert(await page.locator('#communityRecommendationsSetting').count(), 'User-recommendation control is missing.');
  assert(await page.locator('[data-action="saveCommunitySettings"]').count(), 'Community revenue settings cannot be saved.');
  await capture(page, '08g-community-admin', { fullPage: false });
  assert(await page.locator('[data-action="openAssistant"]').count() === 0, 'The obsolete assistant test control is still visible in administration.');
  await clickAdminTab(page, 'campaigns');
  assert(await page.locator('.campaign-admin-card').count() === 1, 'Reward campaigns are missing from management.');
  assert(await page.locator('[data-action="setRewardCampaignStatus"]').count() >= 1, 'Management cannot activate or pause reward campaigns.');
  await page.locator('[data-action="campaignForm"]').first().click();
  assert(await page.locator('#campaignAudience').count() && await page.locator('#campaignTarget').count(), 'Reward campaign editor is incomplete.');
  await page.locator('[data-action="closeModal"]').click();
  await clickAdminTab(page, 'settings');
  assert(await page.locator('.operations-settings').count(), 'Platform operations settings are missing.');
  assert(await page.locator('[data-action="togglePlatformSetting"][data-key="loyaltyEnabled"]').count(), 'Management cannot activate or pause loyalty.');
  assert(await page.locator('#setLoyaltyTargetRequests').count(), 'Management cannot configure the loyalty campaign target.');
  await clickAdminTab(page, 'ads');
  await page.locator('#adImages').setInputFiles(path.join(__dirname, '..', 'app-icon-512.png'));
  await page.locator('[data-action="previewAdDraft"]').click();
  await page.waitForSelector('.ad-preview-device');
  assert(await page.locator('.ad-preview-device').count() === 2, 'Phone and desktop ad previews are missing.');
  await page.locator('[data-action="closeModal"]').click();
  await clickAdminTab(page, 'categoryAdmin');
  assert(await page.locator('.admin-category-visual .kh-subject-art').count() >= 9, 'Management does not show the semantic category artwork.');
  assert(await page.locator('[data-action="toggleCat"]').count() && await page.locator('[data-action="deleteCat"]').count(), 'Category management must expose separate pause and delete controls.');
  assert(await page.locator('[data-action="toggleSvc"]').count() && await page.locator('[data-action="deleteSvc"]').count(), 'Service management must expose separate pause and delete controls.');
  const managedPictograms = await page.locator('.admin-category-visual .kh-subject-art').evaluateAll(items => items.map(item => item.dataset.pictogram));
  assert(managedPictograms.length >= 100 && managedPictograms.every(Boolean), 'Some managed categories or services do not have subject artwork.');
  await page.locator('[data-action="catForm"]').first().click();
  assert(await page.locator('.pictogram-picker .pictogram-option').count() >= 50, 'Category artwork picker is incomplete.');
  const firstArtwork = await page.locator('#catIconKey').inputValue();
  const alternativeArtwork = page.locator('.pictogram-option:not(.active)').first();
  await alternativeArtwork.click();
  assert(await page.locator('#catIconKey').inputValue() !== firstArtwork, 'Selecting category artwork did not update the managed value.');
  await page.locator('[data-action="closeModal"]').click();
  await clickAdminTab(page, 'reports');
  assert(await page.locator('.report-command-bar').count(), 'The production reports command bar is missing.');
  assert(await page.locator('.report-summary-grid .report-summary-card').count() === 4, 'Report decision summaries are incomplete.');
  assert(await page.locator('[data-action="exportReportsCsv"]').count(), 'CSV export is missing from reports.');
  assert(await page.locator('[data-action="exportReportsWord"]').count(), 'Word export is missing from reports.');
  assert(await page.locator('[data-action="printReports"]').count(), 'Print/PDF export is missing from reports.');
  await page.locator('[data-action="printReports"]').click();
  await page.waitForSelector('.report-preview iframe');
  assert(await page.locator('[data-action="closeReportPreview"]').isVisible(), 'Report preview has no visible return control.');
  await page.locator('[data-action="closeReportPreview"]').click();
  assert(await page.locator('.report-command-bar').count(), 'Closing the report preview did not return to reports.');
  await capture(page, '03-admin-reports');
  await clickAdminTab(page, 'quality');
  assert(await page.locator('.system-health').count(), 'System health monitoring panel is missing.');
  await capture(page, '03-admin-quality');
  await clickAdminTab(page, 'platform');
  await page.waitForSelector('.platform-admin-section');
  assert(await page.locator('.platform-feature-list article').count() >= 2, 'Feature rollout controls are missing from administration.');
  assert(await page.locator('[data-action="adminLegalReviewForm"]').count(), 'Legal pathway review queue is missing from administration.');
  await page.locator('[data-action="adminLegalReviewForm"]').first().click();
  assert(await page.locator('.legal-review-summary').count(), 'Legal pathway review details did not open from the queue.');
  await page.locator('[data-action="closeModal"]').click();
  assert(await page.locator('.demand-gap-grid article').count(), 'Aggregate demand gaps are missing from administration.');
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), 'Platform administration overflows the mobile viewport.');
  await capture(page, '03b-admin-platform');

  await page.locator('.topbar [data-action="toggleLang"]').click();
  assert(await page.locator('html').getAttribute('dir') === 'ltr', 'English mode did not switch the document to LTR.');
  assert(await page.locator('.brand').filter({ hasText: /Administration/i }).count(), 'English administration title is missing.');
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'English layout overflows horizontally.');

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
  LOCAL_SERVER?.close();
})().catch(async error => {
  console.error(error);
  LOCAL_SERVER?.close();
  process.exit(1);
});
