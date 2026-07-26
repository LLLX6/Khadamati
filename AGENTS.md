# Khadamati Production Readiness Rules

These rules apply to the entire repository.

## Safety

- Work on `release/production-readiness` or a derived branch. Do not merge into `main`.
- Do not deploy publicly, change production, DNS, paid plans, or external accounts without owner approval.
- Never commit secrets, real `.env` files, database copies, uploads, logs, OTP values, recovery codes, or personal data.
- Do not delete or rewrite existing data. Any destructive migration needs a backup, rollback plan, and owner approval.
- Preserve current users, providers, companies, requests, conversations, notifications, subscriptions, and loyalty data.

## Engineering

- Treat the server and database as the source of truth for private multi-user data.
- Keep Arabic RTL and English LTR behavior working.
- Add backward-compatible defaults for new fields.
- Keep authorization and validation on the server; hiding a control in the UI is not authorization.
- Keep production seed data disabled. Test fixtures belong in isolated test databases.
- Use parameterized SQL, bounded inputs, signed private media, and scoped account data.
- Keep `index.html` and `public/index.html`, the manifests, service workers, icons, and mirrored assets synchronized until the duplication is replaced by a verified build step.

## Verification

- Review `git status` and `git diff` before every commit.
- Run Python compile, unit tests, security API tests, isolated API smoke tests, and UI smoke tests after relevant changes.
- Do not suppress or delete a failing test to make a build pass.
- Record commands, results, limitations, and external actions in `ملفات-الإطلاق/`.
- Use official, current sources for store, government, and legal-readiness research, and label drafts that require professional review.
