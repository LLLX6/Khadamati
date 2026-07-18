# Khadamati smoke tests

Compile and run the domain tests first:

```powershell
python -m py_compile server.py khadamati_domain.py
python -m unittest tests.test_domain -v
```

API smoke test (run against an isolated server and set its admin code):

```powershell
python tests/smoke-api.py
```

Mobile user/provider flow test (requires Playwright and Chrome). When no URL is
provided, this test starts its own static server and blocks stale service workers:

```powershell
node tests/smoke-ui.js
```

Mobile launch performance check:

```powershell
node tests/performance-ui.js
```

The UI flow covers Arabic RTL and English LTR, 320px mobile fit, offer comparison,
consent-gated contact, text/image/voice chat, calendar export, arrival tracking,
provider video, before/after media, subscriptions, finance, and visitor isolation.
The API flow verifies registration, exact matching, waitlists, subscriptions,
contact consent, reviews, and cross-account collaboration.

Set `KHADAMATI_TEST_URL` to test another local or deployed URL.
