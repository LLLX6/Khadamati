# Khadamati smoke tests

Run the server first:

```powershell
python server.py
```

API smoke test:

```powershell
python tests/smoke-api.py
```

Mobile user/provider flow test (requires Playwright and Chrome):

```powershell
node tests/smoke-ui.js
```

Set `KHADAMATI_TEST_URL` to test another local or deployed URL.
