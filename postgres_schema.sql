CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS admin_users (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  code_hash TEXT NOT NULL,
  role TEXT NOT NULL,
  permissions JSONB NOT NULL,
  active BOOLEAN NOT NULL DEFAULT TRUE,
  two_factor_enabled BOOLEAN NOT NULL DEFAULT FALSE,
  two_factor_secret TEXT NOT NULL DEFAULT '',
  recovery_codes JSONB NOT NULL DEFAULT '[]',
  created_at TIMESTAMPTZ DEFAULT now()
);

ALTER TABLE admin_users ADD COLUMN IF NOT EXISTS two_factor_enabled BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE admin_users ADD COLUMN IF NOT EXISTS two_factor_secret TEXT NOT NULL DEFAULT '';
ALTER TABLE admin_users ADD COLUMN IF NOT EXISTS recovery_codes JSONB NOT NULL DEFAULT '[]';

CREATE TABLE IF NOT EXISTS admin_two_factor_challenges (
  id TEXT PRIMARY KEY,
  admin_id TEXT NOT NULL REFERENCES admin_users(id) ON DELETE CASCADE,
  secret_ciphertext TEXT NOT NULL,
  expires_at TIMESTAMPTZ NOT NULL,
  used_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_admin_2fa_challenge
  ON admin_two_factor_challenges(admin_id, expires_at, used_at);

CREATE TABLE IF NOT EXISTS categories (
  id TEXT PRIMARY KEY,
  icon TEXT,
  ar TEXT NOT NULL,
  en TEXT NOT NULL,
  active BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS services (
  id TEXT NOT NULL,
  category_id TEXT NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
  icon TEXT,
  ar TEXT NOT NULL,
  en TEXT NOT NULL,
  active BOOLEAN NOT NULL DEFAULT TRUE,
  PRIMARY KEY(id, category_id)
);

CREATE TABLE IF NOT EXISTS providers (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  phone TEXT NOT NULL,
  gov TEXT,
  wilayah TEXT,
  latitude DOUBLE PRECISION,
  longitude DOUBLE PRECISION,
  location_updated_at TIMESTAMPTZ,
  map_visible BOOLEAN NOT NULL DEFAULT TRUE,
  areas JSONB,
  bio TEXT,
  hours TEXT,
  status TEXT,
  active BOOLEAN,
  verified BOOLEAN,
  featured BOOLEAN,
  package_id TEXT,
  rating NUMERIC,
  reviews INTEGER,
  admin_note TEXT DEFAULT '',
  image_path TEXT DEFAULT '',
  card_image TEXT DEFAULT '',
  pin_hash TEXT DEFAULT '',
  primary_service_id TEXT DEFAULT '',
  listing_enabled BOOLEAN NOT NULL DEFAULT TRUE,
  request_enabled BOOLEAN NOT NULL DEFAULT TRUE,
  services JSONB NOT NULL,
  work_images JSONB DEFAULT '[]',
  documents JSONB DEFAULT '[]',
  quality_score INTEGER DEFAULT 60,
  response_score INTEGER DEFAULT 70,
  quality_breakdown JSONB NOT NULL DEFAULT '{}',
  subscription_until TEXT DEFAULT '',
  stats JSONB NOT NULL DEFAULT '{"views":0,"whatsapp":0,"calls":0}',
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS provider_requests (
  id TEXT PRIMARY KEY,
  payload JSONB NOT NULL,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS leads (
  id TEXT PRIMARY KEY,
  provider_id TEXT REFERENCES providers(id) ON DELETE SET NULL,
  kind TEXT,
  customer_name TEXT,
  phone TEXT,
  note TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS finance (
  id TEXT PRIMARY KEY,
  kind TEXT,
  amount NUMERIC,
  source TEXT,
  note TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS whatsapp_logs (
  id TEXT PRIMARY KEY,
  target TEXT,
  status TEXT,
  detail TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS reviews (
  id TEXT PRIMARY KEY,
  provider_id TEXT NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
  rating INTEGER NOT NULL,
  customer_name TEXT,
  phone TEXT,
  comment TEXT,
  dimensions JSONB NOT NULL DEFAULT '{}',
  approved BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS complaints (
  id TEXT PRIMARY KEY,
  provider_id TEXT REFERENCES providers(id) ON DELETE SET NULL,
  request_id TEXT DEFAULT '',
  user_id TEXT DEFAULT '',
  customer_name TEXT,
  phone TEXT,
  reason TEXT,
  category TEXT DEFAULT '',
  source TEXT DEFAULT 'request',
  detail TEXT,
  status TEXT NOT NULL DEFAULT 'open',
  priority TEXT NOT NULL DEFAULT 'normal',
  resolution TEXT DEFAULT '',
  assigned_admin_id TEXT DEFAULT '',
  due_at TIMESTAMPTZ,
  outcome TEXT DEFAULT '',
  closed_at TIMESTAMPTZ,
  escalation_route TEXT DEFAULT '',
  reopen_count INTEGER NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS provider_verification_cases (
  id TEXT PRIMARY KEY,
  provider_id TEXT NOT NULL UNIQUE REFERENCES providers(id) ON DELETE CASCADE,
  provider_kind TEXT NOT NULL DEFAULT 'individual',
  status TEXT NOT NULL DEFAULT 'unverified',
  level TEXT NOT NULL DEFAULT 'basic',
  identity_status TEXT NOT NULL DEFAULT 'pending',
  entity_status TEXT NOT NULL DEFAULT 'not_applicable',
  activity_status TEXT NOT NULL DEFAULT 'pending',
  requirements JSONB NOT NULL DEFAULT '[]',
  evidence JSONB NOT NULL DEFAULT '[]',
  reviewer_id TEXT DEFAULT '',
  decision_note TEXT DEFAULT '',
  submitted_at TIMESTAMPTZ,
  reviewed_at TIMESTAMPTZ,
  expires_at TIMESTAMPTZ,
  managed BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS trust_case_events (
  id TEXT PRIMARY KEY,
  case_kind TEXT NOT NULL,
  case_id TEXT NOT NULL,
  actor_kind TEXT NOT NULL DEFAULT 'system',
  actor_id TEXT DEFAULT '',
  event_type TEXT NOT NULL,
  from_status TEXT DEFAULT '',
  to_status TEXT DEFAULT '',
  message TEXT DEFAULT '',
  metadata JSONB NOT NULL DEFAULT '{}',
  visible_to_subject BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS complaint_evidence (
  id TEXT PRIMARY KEY,
  complaint_id TEXT NOT NULL REFERENCES complaints(id) ON DELETE CASCADE,
  uploader_kind TEXT NOT NULL,
  uploader_id TEXT NOT NULL,
  media_path TEXT NOT NULL,
  media_type TEXT NOT NULL DEFAULT 'document',
  label TEXT DEFAULT '',
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS interaction_blocks (
  id TEXT PRIMARY KEY,
  blocker_kind TEXT NOT NULL,
  blocker_id TEXT NOT NULL,
  blocked_kind TEXT NOT NULL,
  blocked_id TEXT NOT NULL,
  request_id TEXT DEFAULT '',
  reason TEXT DEFAULT '',
  active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_verification_status
  ON provider_verification_cases(status, expires_at);
CREATE INDEX IF NOT EXISTS idx_trust_events_case
  ON trust_case_events(case_kind, case_id, created_at);
CREATE INDEX IF NOT EXISTS idx_complaint_evidence_case
  ON complaint_evidence(complaint_id, created_at);
CREATE INDEX IF NOT EXISTS idx_interaction_blocks_actor
  ON interaction_blocks(blocker_kind, blocker_id, active);
CREATE INDEX IF NOT EXISTS idx_interaction_blocks_target
  ON interaction_blocks(blocked_kind, blocked_id, active);
CREATE UNIQUE INDEX IF NOT EXISTS idx_interaction_blocks_unique_active
  ON interaction_blocks(blocker_kind, blocker_id, blocked_kind, blocked_id)
  WHERE active = TRUE;

CREATE TABLE IF NOT EXISTS packages (
  id TEXT PRIMARY KEY,
  ar TEXT NOT NULL,
  en TEXT NOT NULL,
  price NUMERIC NOT NULL DEFAULT 0,
  duration_days INTEGER NOT NULL DEFAULT 30,
  featured_boost INTEGER NOT NULL DEFAULT 0,
  max_services INTEGER NOT NULL DEFAULT 3,
  max_categories INTEGER NOT NULL DEFAULT 1,
  max_wilayats INTEGER NOT NULL DEFAULT 5,
  max_images INTEGER NOT NULL DEFAULT 5,
  active BOOLEAN NOT NULL DEFAULT TRUE
);

-- Safe parity additions when applying this file to an existing PostgreSQL staging database.
ALTER TABLE providers ADD COLUMN IF NOT EXISTS latitude DOUBLE PRECISION;
ALTER TABLE providers ADD COLUMN IF NOT EXISTS longitude DOUBLE PRECISION;
ALTER TABLE providers ADD COLUMN IF NOT EXISTS location_updated_at TIMESTAMPTZ;
ALTER TABLE providers ADD COLUMN IF NOT EXISTS map_visible BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE providers ADD COLUMN IF NOT EXISTS primary_service_id TEXT DEFAULT '';
ALTER TABLE providers ADD COLUMN IF NOT EXISTS listing_enabled BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE providers ADD COLUMN IF NOT EXISTS request_enabled BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE providers ADD COLUMN IF NOT EXISTS provider_type TEXT DEFAULT 'individual';
ALTER TABLE providers ADD COLUMN IF NOT EXISTS verification_expiry TIMESTAMPTZ;
ALTER TABLE providers ADD COLUMN IF NOT EXISTS quality_breakdown JSONB NOT NULL DEFAULT '{}';
ALTER TABLE packages ADD COLUMN IF NOT EXISTS max_categories INTEGER NOT NULL DEFAULT 1;
ALTER TABLE packages ADD COLUMN IF NOT EXISTS max_wilayats INTEGER NOT NULL DEFAULT 5;
ALTER TABLE complaints ADD COLUMN IF NOT EXISTS request_id TEXT DEFAULT '';
ALTER TABLE complaints ADD COLUMN IF NOT EXISTS user_id TEXT DEFAULT '';
ALTER TABLE complaints ADD COLUMN IF NOT EXISTS category TEXT DEFAULT '';
ALTER TABLE complaints ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'request';
ALTER TABLE complaints ADD COLUMN IF NOT EXISTS assigned_admin_id TEXT DEFAULT '';
ALTER TABLE complaints ADD COLUMN IF NOT EXISTS due_at TIMESTAMPTZ;
ALTER TABLE complaints ADD COLUMN IF NOT EXISTS outcome TEXT DEFAULT '';
ALTER TABLE complaints ADD COLUMN IF NOT EXISTS closed_at TIMESTAMPTZ;
ALTER TABLE complaints ADD COLUMN IF NOT EXISTS escalation_route TEXT DEFAULT '';
ALTER TABLE complaints ADD COLUMN IF NOT EXISTS reopen_count INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS subscriptions (
  id TEXT PRIMARY KEY,
  provider_id TEXT NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
  package_id TEXT NOT NULL REFERENCES packages(id),
  amount NUMERIC NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'pending',
  start_date TEXT,
  end_date TEXT,
  note TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS payments (
  id TEXT PRIMARY KEY,
  provider_id TEXT REFERENCES providers(id) ON DELETE SET NULL,
  subscription_id TEXT REFERENCES subscriptions(id) ON DELETE SET NULL,
  kind TEXT NOT NULL DEFAULT 'revenue',
  amount NUMERIC NOT NULL DEFAULT 0,
  method TEXT DEFAULT 'manual',
  status TEXT NOT NULL DEFAULT 'paid',
  note TEXT DEFAULT '',
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS audit_logs (
  id TEXT PRIMARY KEY,
  actor_kind TEXT,
  actor_id TEXT,
  action TEXT NOT NULL,
  target TEXT,
  detail TEXT DEFAULT '',
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS app_users (
  id TEXT PRIMARY KEY,
  phone TEXT NOT NULL UNIQUE,
  name TEXT DEFAULT '',
  pin_hash TEXT DEFAULT '',
  gov TEXT DEFAULT '',
  wilayah TEXT DEFAULT '',
  avatar TEXT DEFAULT '',
  latitude DOUBLE PRECISION,
  longitude DOUBLE PRECISION,
  status TEXT NOT NULL DEFAULT 'active',
  failed_attempts INTEGER NOT NULL DEFAULT 0,
  locked_until TIMESTAMPTZ,
  first_login TIMESTAMPTZ DEFAULT now(),
  last_login TIMESTAMPTZ DEFAULT now(),
  login_count INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS auth_sessions (
  id TEXT PRIMARY KEY,
  token_hash TEXT NOT NULL UNIQUE,
  session_json JSONB NOT NULL,
  expires_at TIMESTAMPTZ NOT NULL,
  revoked BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS customer_requests (
  id TEXT PRIMARY KEY,
  user_id TEXT REFERENCES app_users(id) ON DELETE SET NULL,
  customer_name TEXT DEFAULT '',
  phone TEXT DEFAULT '',
  service_value TEXT NOT NULL,
  service_name TEXT DEFAULT '',
  gov TEXT DEFAULT '',
  wilayah TEXT DEFAULT '',
  latitude DOUBLE PRECISION,
  longitude DOUBLE PRECISION,
  urgency TEXT DEFAULT 'normal',
  schedule_type TEXT DEFAULT 'flexible',
  requested_at TIMESTAMPTZ,
  budget_min NUMERIC DEFAULT 0,
  budget_max NUMERIC DEFAULT 0,
  location_text TEXT DEFAULT '',
  note TEXT DEFAULT '',
  images JSONB DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'matching',
  accepted_provider_id TEXT REFERENCES providers(id) ON DELETE SET NULL,
  matching_provider_ids JSONB DEFAULT '[]',
  declined_provider_ids JSONB DEFAULT '[]',
  offers_open BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS app_notifications (
  id TEXT PRIMARY KEY,
  target_kind TEXT NOT NULL,
  target_id TEXT DEFAULT '',
  type TEXT DEFAULT 'general',
  title TEXT NOT NULL,
  message TEXT DEFAULT '',
  related_id TEXT DEFAULT '',
  priority TEXT DEFAULT 'normal',
  action_text TEXT DEFAULT '',
  action_route TEXT DEFAULT '',
  is_read BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS advertisements (
  id TEXT PRIMARY KEY,
  image_path TEXT NOT NULL,
  advertiser TEXT DEFAULT '',
  phone TEXT DEFAULT '',
  amount NUMERIC DEFAULT 0,
  title TEXT DEFAULT '',
  body TEXT DEFAULT '',
  starts_at TIMESTAMPTZ,
  ends_at TIMESTAMPTZ,
  active BOOLEAN NOT NULL DEFAULT TRUE,
  deleted_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS password_recoveries (
  id TEXT PRIMARY KEY,
  account_kind TEXT NOT NULL,
  account_id TEXT DEFAULT '',
  phone TEXT NOT NULL,
  code_hash TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  expires_at TIMESTAMPTZ NOT NULL,
  verified_at TIMESTAMPTZ,
  reset_token_hash TEXT DEFAULT '',
  used_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS admin_email_challenges (
  id TEXT PRIMARY KEY,
  admin_id TEXT NOT NULL REFERENCES admin_users(id) ON DELETE CASCADE,
  code_hash TEXT NOT NULL,
  request_key TEXT DEFAULT '',
  attempts INTEGER NOT NULL DEFAULT 0,
  expires_at TIMESTAMPTZ NOT NULL,
  used_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_admin_email_challenge
  ON admin_email_challenges(admin_id, expires_at, used_at);

CREATE TABLE IF NOT EXISTS push_subscriptions (
  id TEXT PRIMARY KEY,
  target_kind TEXT NOT NULL,
  target_id TEXT DEFAULT '',
  endpoint TEXT NOT NULL UNIQUE,
  subscription_json JSONB NOT NULL,
  active BOOLEAN NOT NULL DEFAULT TRUE,
  last_success_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS policy_acceptances (
  id TEXT PRIMARY KEY,
  user_id TEXT DEFAULT '',
  phone TEXT DEFAULT '',
  policy_version TEXT NOT NULL,
  accepted_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_requests_status ON customer_requests(status, created_at);
CREATE INDEX IF NOT EXISTS idx_notifications_target ON app_notifications(target_kind, target_id, is_read);
CREATE INDEX IF NOT EXISTS idx_sessions_hash ON auth_sessions(token_hash, expires_at);

ALTER TABLE providers ADD COLUMN IF NOT EXISTS subscription_start TEXT DEFAULT '';
ALTER TABLE providers ADD COLUMN IF NOT EXISTS provider_type TEXT DEFAULT 'individual';
ALTER TABLE providers ADD COLUMN IF NOT EXISTS company_name TEXT DEFAULT '';
ALTER TABLE providers ADD COLUMN IF NOT EXISTS company_id TEXT DEFAULT '';
ALTER TABLE providers ADD COLUMN IF NOT EXISTS commercial_no TEXT DEFAULT '';
ALTER TABLE providers ADD COLUMN IF NOT EXISTS verification_expiry TEXT DEFAULT '';
ALTER TABLE providers ADD COLUMN IF NOT EXISTS commercial_expiry TEXT DEFAULT '';
ALTER TABLE providers ADD COLUMN IF NOT EXISTS license_expiry TEXT DEFAULT '';
ALTER TABLE providers ADD COLUMN IF NOT EXISTS latitude DOUBLE PRECISION;
ALTER TABLE providers ADD COLUMN IF NOT EXISTS longitude DOUBLE PRECISION;
ALTER TABLE providers ADD COLUMN IF NOT EXISTS location_updated_at TIMESTAMPTZ;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS service_value TEXT DEFAULT '';
ALTER TABLE leads ADD COLUMN IF NOT EXISTS service_name TEXT DEFAULT '';
ALTER TABLE leads ADD COLUMN IF NOT EXISTS gov TEXT DEFAULT '';
ALTER TABLE leads ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'open';

-- Khadamati subscription, consent, marketplace, and security domain.
ALTER TABLE providers ADD COLUMN IF NOT EXISTS before_after JSONB DEFAULT '[]';
ALTER TABLE providers ADD COLUMN IF NOT EXISTS intro_video_url TEXT DEFAULT '';
ALTER TABLE providers ADD COLUMN IF NOT EXISTS listing_enabled BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE providers ADD COLUMN IF NOT EXISTS request_enabled BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE providers ADD COLUMN IF NOT EXISTS subscription_state TEXT DEFAULT 'active';
ALTER TABLE providers ADD COLUMN IF NOT EXISTS availability JSONB DEFAULT '{}';
ALTER TABLE providers ADD COLUMN IF NOT EXISTS response_minutes INTEGER NOT NULL DEFAULT 30;
ALTER TABLE providers ADD COLUMN IF NOT EXISTS completed_jobs INTEGER NOT NULL DEFAULT 0;
ALTER TABLE providers ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT now();
ALTER TABLE app_users ADD COLUMN IF NOT EXISTS location_updated_at TIMESTAMPTZ;
ALTER TABLE app_users ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT now();
ALTER TABLE customer_requests ADD COLUMN IF NOT EXISTS offers JSONB DEFAULT '[]';
ALTER TABLE customer_requests ADD COLUMN IF NOT EXISTS messages JSONB DEFAULT '[]';
ALTER TABLE customer_requests ADD COLUMN IF NOT EXISTS arrival JSONB DEFAULT '{}';
ALTER TABLE customer_requests ADD COLUMN IF NOT EXISTS contact_consent JSONB DEFAULT '{}';
ALTER TABLE customer_requests ADD COLUMN IF NOT EXISTS waitlisted BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE customer_requests ADD COLUMN IF NOT EXISTS marketplace_status TEXT DEFAULT 'pending';
ALTER TABLE customer_requests ADD COLUMN IF NOT EXISTS dispatch_started_at TIMESTAMPTZ;
ALTER TABLE customer_requests ADD COLUMN IF NOT EXISTS expansion_at TIMESTAMPTZ;
ALTER TABLE customer_requests ADD COLUMN IF NOT EXISTS ranking_version TEXT DEFAULT '';

ALTER TABLE packages ADD COLUMN IF NOT EXISTS currency TEXT NOT NULL DEFAULT 'OMR';
ALTER TABLE packages ADD COLUMN IF NOT EXISTS max_wilayats INTEGER NOT NULL DEFAULT 5;
ALTER TABLE packages ADD COLUMN IF NOT EXISTS max_governorates INTEGER NOT NULL DEFAULT 1;
ALTER TABLE packages ADD COLUMN IF NOT EXISTS monthly_response_limit INTEGER NOT NULL DEFAULT 30;
ALTER TABLE packages ADD COLUMN IF NOT EXISTS lead_delay_minutes INTEGER NOT NULL DEFAULT 15;
ALTER TABLE packages ADD COLUMN IF NOT EXISTS max_team_members INTEGER NOT NULL DEFAULT 1;
ALTER TABLE packages ADD COLUMN IF NOT EXISTS max_branches INTEGER NOT NULL DEFAULT 1;
ALTER TABLE packages ADD COLUMN IF NOT EXISTS shared_inbox BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE packages ADD COLUMN IF NOT EXISTS advanced_reports BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE packages ADD COLUMN IF NOT EXISTS badge_ar TEXT DEFAULT '';
ALTER TABLE packages ADD COLUMN IF NOT EXISTS badge_en TEXT DEFAULT '';
ALTER TABLE packages ADD COLUMN IF NOT EXISTS foundation_once BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE packages ADD COLUMN IF NOT EXISTS verified_required BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE packages ADD COLUMN IF NOT EXISTS legacy BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE packages ADD COLUMN IF NOT EXISTS entitlements JSONB DEFAULT '{}';

ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS currency TEXT NOT NULL DEFAULT 'OMR';
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS grace_days INTEGER NOT NULL DEFAULT 14;
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS renewal_package_id TEXT DEFAULT '';
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS previous_package_id TEXT DEFAULT '';
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS proration_amount NUMERIC NOT NULL DEFAULT 0;
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS credit_amount NUMERIC NOT NULL DEFAULT 0;
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS activated_at TIMESTAMPTZ;
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS grace_until TIMESTAMPTZ;
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS cancelled_at TIMESTAMPTZ;
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS refunded_at TIMESTAMPTZ;
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS payment_id TEXT DEFAULT '';
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS auto_renew BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS legacy_package_id TEXT DEFAULT '';
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS metadata JSONB DEFAULT '{}';
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT now();

ALTER TABLE payments ADD COLUMN IF NOT EXISTS currency TEXT NOT NULL DEFAULT 'OMR';
ALTER TABLE payments ADD COLUMN IF NOT EXISTS external_id TEXT DEFAULT '';
ALTER TABLE payments ADD COLUMN IF NOT EXISTS gateway TEXT DEFAULT 'manual';
ALTER TABLE payments ADD COLUMN IF NOT EXISTS failure_code TEXT DEFAULT '';
ALTER TABLE payments ADD COLUMN IF NOT EXISTS verified_at TIMESTAMPTZ;
ALTER TABLE payments ADD COLUMN IF NOT EXISTS refunded_at TIMESTAMPTZ;
ALTER TABLE payments ADD COLUMN IF NOT EXISTS metadata JSONB DEFAULT '{}';
ALTER TABLE payments ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT now();

ALTER TABLE policy_acceptances ADD COLUMN IF NOT EXISTS document_types JSONB DEFAULT '[]';
ALTER TABLE policy_acceptances ADD COLUMN IF NOT EXISTS language TEXT DEFAULT 'ar';
ALTER TABLE policy_acceptances ADD COLUMN IF NOT EXISTS withdrawn_at TIMESTAMPTZ;
ALTER TABLE policy_acceptances ADD COLUMN IF NOT EXISTS metadata JSONB DEFAULT '{}';
ALTER TABLE reviews ADD COLUMN IF NOT EXISTS request_id TEXT DEFAULT '';
ALTER TABLE reviews ADD COLUMN IF NOT EXISTS user_id TEXT DEFAULT '';
ALTER TABLE reviews ADD COLUMN IF NOT EXISTS dimensions JSONB NOT NULL DEFAULT '{}';
ALTER TABLE complaints ADD COLUMN IF NOT EXISTS request_id TEXT DEFAULT '';
ALTER TABLE complaints ADD COLUMN IF NOT EXISTS user_id TEXT DEFAULT '';

CREATE TABLE IF NOT EXISTS subscription_events (
  id TEXT PRIMARY KEY, subscription_id TEXT NOT NULL, event_type TEXT NOT NULL,
  from_state TEXT DEFAULT '', to_state TEXT DEFAULT '', actor TEXT DEFAULT 'system',
  detail TEXT DEFAULT '', created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS foundation_claims (
  id TEXT PRIMARY KEY, provider_id TEXT NOT NULL UNIQUE, phone TEXT DEFAULT '',
  commercial_no TEXT DEFAULT '', fingerprint TEXT NOT NULL UNIQUE,
  subscription_id TEXT NOT NULL, created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS contact_consents (
  id TEXT PRIMARY KEY, request_id TEXT NOT NULL, user_id TEXT NOT NULL,
  provider_id TEXT NOT NULL, channel TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'revoked',
  granted_at TIMESTAMPTZ, expires_at TIMESTAMPTZ, revoked_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT now(), updated_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(request_id, provider_id, channel)
);

CREATE TABLE IF NOT EXISTS request_dispatches (
  id TEXT PRIMARY KEY, request_id TEXT NOT NULL, provider_id TEXT NOT NULL,
  rank INTEGER NOT NULL DEFAULT 0, score NUMERIC NOT NULL DEFAULT 0,
  score_breakdown JSONB DEFAULT '{}', wave INTEGER NOT NULL DEFAULT 1,
  release_at TIMESTAMPTZ NOT NULL, status TEXT NOT NULL DEFAULT 'scheduled',
  notified_at TIMESTAMPTZ, opened_at TIMESTAMPTZ, offered_at TIMESTAMPTZ,
  accepted_at TIMESTAMPTZ, created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now(), UNIQUE(request_id, provider_id)
);

CREATE TABLE IF NOT EXISTS invoices (
  id TEXT PRIMARY KEY, payment_id TEXT NOT NULL UNIQUE, subscription_id TEXT NOT NULL,
  provider_id TEXT NOT NULL, number TEXT NOT NULL UNIQUE, currency TEXT NOT NULL DEFAULT 'OMR',
  subtotal NUMERIC NOT NULL DEFAULT 0, total NUMERIC NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'issued', issued_at TIMESTAMPTZ NOT NULL,
  paid_at TIMESTAMPTZ, metadata JSONB DEFAULT '{}', created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS coupons (
  id TEXT PRIMARY KEY, code TEXT NOT NULL UNIQUE, name_ar TEXT DEFAULT '', name_en TEXT DEFAULT '',
  discount_type TEXT NOT NULL DEFAULT 'fixed', discount_value NUMERIC NOT NULL DEFAULT 0,
  applies_to JSONB DEFAULT '[]', starts_at TIMESTAMPTZ, ends_at TIMESTAMPTZ,
  max_uses INTEGER NOT NULL DEFAULT 0, uses_count INTEGER NOT NULL DEFAULT 0,
  active BOOLEAN NOT NULL DEFAULT TRUE, created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS coupon_redemptions (
  id TEXT PRIMARY KEY, coupon_id TEXT NOT NULL, provider_id TEXT NOT NULL,
  subscription_id TEXT DEFAULT '', amount NUMERIC NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT now(), UNIQUE(coupon_id, provider_id, subscription_id)
);

CREATE TABLE IF NOT EXISTS campaigns (
  id TEXT PRIMARY KEY, name_ar TEXT NOT NULL, name_en TEXT DEFAULT '',
  kind TEXT NOT NULL DEFAULT 'subscription', starts_at TIMESTAMPTZ, ends_at TIMESTAMPTZ,
  budget NUMERIC NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'draft',
  rules JSONB DEFAULT '{}', created_at TIMESTAMPTZ DEFAULT now(), updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS provider_promotions (
  id TEXT PRIMARY KEY, provider_id TEXT NOT NULL, campaign_id TEXT DEFAULT '',
  kind TEXT NOT NULL DEFAULT 'featured', area TEXT DEFAULT '', service_value TEXT DEFAULT '',
  starts_at TIMESTAMPTZ, ends_at TIMESTAMPTZ, amount NUMERIC NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'pending_payment', created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS provider_team_members (
  id TEXT PRIMARY KEY, provider_id TEXT NOT NULL, name TEXT NOT NULL, phone TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'provider_staff', pin_hash TEXT NOT NULL DEFAULT '',
  permissions JSONB DEFAULT '[]', active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ DEFAULT now(), updated_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(provider_id, phone)
);

CREATE TABLE IF NOT EXISTS provider_branches (
  id TEXT PRIMARY KEY, provider_id TEXT NOT NULL, name TEXT NOT NULL,
  gov TEXT DEFAULT '', wilayah TEXT DEFAULT '', address TEXT DEFAULT '',
  latitude DOUBLE PRECISION, longitude DOUBLE PRECISION, phone TEXT DEFAULT '',
  active BOOLEAN NOT NULL DEFAULT TRUE, created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS otp_challenges (
  id TEXT PRIMARY KEY, phone TEXT NOT NULL, purpose TEXT NOT NULL,
  target_kind TEXT NOT NULL DEFAULT 'user', code_hash TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL DEFAULT 5,
  expires_at TIMESTAMPTZ NOT NULL, verified_at TIMESTAMPTZ,
  delivery_status TEXT DEFAULT 'pending', created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS webhook_events (
  id TEXT PRIMARY KEY, provider TEXT NOT NULL, event_id TEXT NOT NULL UNIQUE,
  signature_valid BOOLEAN NOT NULL DEFAULT FALSE, payload_hash TEXT NOT NULL,
  processed BOOLEAN NOT NULL DEFAULT FALSE, created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS community_listings (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('wanted','package')),
  owner_kind TEXT NOT NULL CHECK (owner_kind IN ('user','provider','company')),
  owner_id TEXT NOT NULL,
  title TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  category_id TEXT NOT NULL,
  service_value TEXT NOT NULL,
  budget_min NUMERIC NOT NULL DEFAULT 0,
  budget_max NUMERIC NOT NULL DEFAULT 0,
  price_amount NUMERIC NOT NULL DEFAULT 0,
  billing_period TEXT NOT NULL DEFAULT 'one_time',
  duration_text TEXT DEFAULT '',
  gov TEXT DEFAULT '',
  wilayah TEXT DEFAULT '',
  latitude DOUBLE PRECISION,
  longitude DOUBLE PRECISION,
  location_text TEXT DEFAULT '',
  image_path TEXT DEFAULT '',
  details JSONB NOT NULL DEFAULT '{}',
  contact_channels JSONB NOT NULL DEFAULT '["app"]',
  status TEXT NOT NULL DEFAULT 'draft',
  moderation_note TEXT DEFAULT '',
  billing_status TEXT NOT NULL DEFAULT 'included',
  featured BOOLEAN NOT NULL DEFAULT FALSE,
  expires_at TIMESTAMPTZ,
  published_at TIMESTAMPTZ,
  closed_at TIMESTAMPTZ,
  deleted_at TIMESTAMPTZ,
  request_id TEXT DEFAULT '',
  idempotency_key TEXT NOT NULL,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(owner_kind, owner_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS community_offers (
  id TEXT PRIMARY KEY,
  listing_id TEXT NOT NULL REFERENCES community_listings(id),
  provider_id TEXT NOT NULL,
  amount NUMERIC NOT NULL DEFAULT 0,
  duration_text TEXT DEFAULT '',
  note TEXT DEFAULT '',
  status TEXT NOT NULL DEFAULT 'sent',
  request_id TEXT DEFAULT '',
  idempotency_key TEXT NOT NULL,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(listing_id, provider_id),
  UNIQUE(provider_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS community_orders (
  id TEXT PRIMARY KEY,
  listing_id TEXT NOT NULL REFERENCES community_listings(id),
  user_id TEXT NOT NULL,
  request_id TEXT DEFAULT '',
  status TEXT NOT NULL DEFAULT 'created',
  snapshot JSONB NOT NULL DEFAULT '{}',
  idempotency_key TEXT NOT NULL,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(user_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS community_favorites (
  id TEXT PRIMARY KEY,
  account_kind TEXT NOT NULL,
  account_id TEXT NOT NULL,
  listing_id TEXT NOT NULL REFERENCES community_listings(id),
  created_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(account_kind, account_id, listing_id)
);

CREATE TABLE IF NOT EXISTS community_reports (
  id TEXT PRIMARY KEY,
  listing_id TEXT NOT NULL REFERENCES community_listings(id),
  reporter_kind TEXT NOT NULL,
  reporter_id TEXT NOT NULL,
  reason TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',
  resolution TEXT DEFAULT '',
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(listing_id, reporter_kind, reporter_id)
);

CREATE TABLE IF NOT EXISTS community_events (
  id TEXT PRIMARY KEY,
  listing_id TEXT NOT NULL REFERENCES community_listings(id),
  actor_kind TEXT NOT NULL,
  actor_id TEXT DEFAULT '',
  action TEXT NOT NULL,
  detail JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_dispatch_release ON request_dispatches(status, release_at, wave);
CREATE INDEX IF NOT EXISTS idx_dispatch_provider ON request_dispatches(provider_id, status, notified_at);
CREATE INDEX IF NOT EXISTS idx_consent_lookup ON contact_consents(request_id, provider_id, channel, status);
CREATE INDEX IF NOT EXISTS idx_subscription_provider ON subscriptions(provider_id, status, end_date);
CREATE INDEX IF NOT EXISTS idx_payment_subscription ON payments(subscription_id, status);
CREATE INDEX IF NOT EXISTS idx_otp_phone ON otp_challenges(phone, purpose, created_at);
CREATE INDEX IF NOT EXISTS idx_community_feed
  ON community_listings(kind, status, featured, created_at);
CREATE INDEX IF NOT EXISTS idx_community_owner
  ON community_listings(owner_kind, owner_id, status, updated_at);
CREATE INDEX IF NOT EXISTS idx_community_service
  ON community_listings(service_value, status, expires_at);
CREATE INDEX IF NOT EXISTS idx_community_expiry
  ON community_listings(status, expires_at);
CREATE INDEX IF NOT EXISTS idx_community_offer_listing
  ON community_offers(listing_id, status, updated_at);
CREATE INDEX IF NOT EXISTS idx_community_reports_status
  ON community_reports(status, created_at);
CREATE TABLE IF NOT EXISTS provider_invitations (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
  request_id TEXT NOT NULL REFERENCES customer_requests(id) ON DELETE CASCADE,
  phone TEXT NOT NULL,
  token_hash TEXT NOT NULL UNIQUE,
  provider_id TEXT DEFAULT '',
  provider_request_id TEXT DEFAULT '',
  status TEXT NOT NULL DEFAULT 'pending',
  expires_at TIMESTAMPTZ NOT NULL,
  matched_at TIMESTAMPTZ,
  cancelled_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(user_id, request_id, phone)
);

CREATE INDEX IF NOT EXISTS idx_provider_invitation_phone
  ON provider_invitations(phone, status, expires_at);
CREATE INDEX IF NOT EXISTS idx_provider_invitation_owner
  ON provider_invitations(user_id, created_at);

-- Production-readiness marketplace platform (backward-compatible additions).
ALTER TABLE customer_requests ADD COLUMN IF NOT EXISTS organization_id TEXT DEFAULT '';
ALTER TABLE customer_requests ADD COLUMN IF NOT EXISTS organization_location_id TEXT DEFAULT '';
ALTER TABLE customer_requests ADD COLUMN IF NOT EXISTS requested_by_member_id TEXT DEFAULT '';

CREATE TABLE IF NOT EXISTS conversation_threads (
  request_id TEXT PRIMARY KEY REFERENCES customer_requests(id) ON DELETE CASCADE,
  status TEXT NOT NULL DEFAULT 'open',
  ended_by_kind TEXT NOT NULL DEFAULT '', ended_by_id TEXT NOT NULL DEFAULT '',
  end_reason TEXT NOT NULL DEFAULT '', ended_at TIMESTAMPTZ,
  reopened_at TIMESTAMPTZ, updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS conversation_preferences (
  request_id TEXT NOT NULL REFERENCES customer_requests(id) ON DELETE CASCADE,
  actor_kind TEXT NOT NULL, actor_id TEXT NOT NULL, muted_until TIMESTAMPTZ,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY(request_id, actor_kind, actor_id)
);

CREATE TABLE IF NOT EXISTS provider_legal_profiles (
  provider_id TEXT PRIMARY KEY REFERENCES providers(id) ON DELETE CASCADE,
  pathway TEXT NOT NULL DEFAULT 'individual_omani', nationality TEXT NOT NULL DEFAULT '',
  residency_status TEXT NOT NULL DEFAULT 'not_applicable', employer_name TEXT NOT NULL DEFAULT '',
  employer_authorization_status TEXT NOT NULL DEFAULT 'not_applicable',
  work_permit_expiry DATE, residency_expiry DATE, commercial_expiry DATE,
  activity_license_expiry DATE, review_status TEXT NOT NULL DEFAULT 'pending',
  review_note TEXT NOT NULL DEFAULT '', reviewed_by TEXT NOT NULL DEFAULT '',
  reviewed_at TIMESTAMPTZ, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS customer_organizations (
  id TEXT PRIMARY KEY, owner_user_id TEXT NOT NULL REFERENCES app_users(id),
  name TEXT NOT NULL, organization_type TEXT NOT NULL DEFAULT 'business',
  status TEXT NOT NULL DEFAULT 'active', approval_mode TEXT NOT NULL DEFAULT 'owner',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS organization_members (
  id TEXT PRIMARY KEY, organization_id TEXT NOT NULL REFERENCES customer_organizations(id) ON DELETE CASCADE,
  user_id TEXT DEFAULT '', name TEXT NOT NULL, phone TEXT NOT NULL DEFAULT '',
  role TEXT NOT NULL DEFAULT 'requester', active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(organization_id, phone)
);

CREATE TABLE IF NOT EXISTS organization_locations (
  id TEXT PRIMARY KEY, organization_id TEXT NOT NULL REFERENCES customer_organizations(id) ON DELETE CASCADE,
  name TEXT NOT NULL, gov TEXT NOT NULL DEFAULT '', wilayah TEXT NOT NULL DEFAULT '',
  address TEXT NOT NULL DEFAULT '', active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS maintenance_contracts (
  id TEXT PRIMARY KEY, owner_user_id TEXT NOT NULL REFERENCES app_users(id),
  organization_id TEXT DEFAULT '', provider_id TEXT NOT NULL REFERENCES providers(id),
  request_id TEXT DEFAULT '', service_asset_id TEXT DEFAULT '', service_value TEXT NOT NULL,
  title TEXT NOT NULL, frequency_days INTEGER NOT NULL CHECK(frequency_days BETWEEN 1 AND 730),
  amount NUMERIC NOT NULL DEFAULT 0, next_due_at TIMESTAMPTZ NOT NULL,
  auto_renew BOOLEAN NOT NULL DEFAULT FALSE, status TEXT NOT NULL DEFAULT 'active',
  last_completed_at TIMESTAMPTZ, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS provider_crm_records (
  id TEXT PRIMARY KEY, provider_id TEXT NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
  user_id TEXT NOT NULL, request_id TEXT NOT NULL REFERENCES customer_requests(id),
  display_name TEXT NOT NULL DEFAULT '', stage TEXT NOT NULL DEFAULT 'active',
  next_action_at TIMESTAMPTZ, note TEXT NOT NULL DEFAULT '',
  invoice_status TEXT NOT NULL DEFAULT 'not_issued', warranty_until TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(provider_id, request_id)
);

CREATE TABLE IF NOT EXISTS referrals (
  id TEXT PRIMARY KEY, code TEXT NOT NULL UNIQUE, referrer_kind TEXT NOT NULL,
  referrer_id TEXT NOT NULL, referred_kind TEXT NOT NULL DEFAULT '', referred_id TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'created', risk_status TEXT NOT NULL DEFAULT 'clear',
  qualified_at TIMESTAMPTZ, reward_status TEXT NOT NULL DEFAULT 'not_eligible',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(referred_kind, referred_id)
);

CREATE TABLE IF NOT EXISTS training_modules (
  id TEXT PRIMARY KEY, title_ar TEXT NOT NULL, title_en TEXT NOT NULL DEFAULT '',
  audience TEXT NOT NULL DEFAULT 'provider', content_ar TEXT NOT NULL DEFAULT '',
  content_en TEXT NOT NULL DEFAULT '', pass_score INTEGER NOT NULL DEFAULT 70,
  sort_order INTEGER NOT NULL DEFAULT 0, active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS training_progress (
  module_id TEXT NOT NULL REFERENCES training_modules(id) ON DELETE CASCADE,
  provider_id TEXT NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
  score INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'started',
  completed_at TIMESTAMPTZ, updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY(module_id, provider_id)
);

CREATE TABLE IF NOT EXISTS provider_achievements (
  id TEXT PRIMARY KEY, provider_id TEXT NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
  code TEXT NOT NULL, evidence JSONB NOT NULL DEFAULT '{}', earned_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(provider_id, code)
);

CREATE TABLE IF NOT EXISTS demand_alerts (
  id TEXT PRIMARY KEY, target_kind TEXT NOT NULL, target_id TEXT NOT NULL,
  service_value TEXT NOT NULL, gov TEXT NOT NULL DEFAULT '', wilayah TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'active', last_notified_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(target_kind, target_id, service_value, gov, wilayah)
);

CREATE TABLE IF NOT EXISTS platform_feature_flags (
  key TEXT PRIMARY KEY, enabled BOOLEAN NOT NULL DEFAULT FALSE,
  rollout_percentage INTEGER NOT NULL DEFAULT 0 CHECK(rollout_percentage BETWEEN 0 AND 100),
  audiences JSONB NOT NULL DEFAULT '[]', config JSONB NOT NULL DEFAULT '{}',
  updated_by TEXT NOT NULL DEFAULT '', updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS risk_reviews (
  id TEXT PRIMARY KEY, subject_kind TEXT NOT NULL, subject_id TEXT NOT NULL,
  signal_type TEXT NOT NULL, score INTEGER NOT NULL DEFAULT 0 CHECK(score BETWEEN 0 AND 100),
  signals JSONB NOT NULL DEFAULT '[]', status TEXT NOT NULL DEFAULT 'pending_review',
  reviewer_id TEXT NOT NULL DEFAULT '', decision TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS enterprise_api_clients (
  id TEXT PRIMARY KEY, organization_id TEXT NOT NULL REFERENCES customer_organizations(id),
  name TEXT NOT NULL, key_prefix TEXT NOT NULL, key_hash TEXT NOT NULL UNIQUE,
  scopes JSONB NOT NULL DEFAULT '[]', rate_limit INTEGER NOT NULL DEFAULT 60,
  active BOOLEAN NOT NULL DEFAULT TRUE, expires_at TIMESTAMPTZ, last_used_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS enterprise_api_audit (
  id TEXT PRIMARY KEY, client_id TEXT NOT NULL REFERENCES enterprise_api_clients(id) ON DELETE CASCADE,
  scope TEXT NOT NULL, resource TEXT NOT NULL DEFAULT '', created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS integration_adapters (
  key TEXT PRIMARY KEY, kind TEXT NOT NULL, enabled BOOLEAN NOT NULL DEFAULT FALSE,
  mode TEXT NOT NULL DEFAULT 'disabled', legal_status TEXT NOT NULL DEFAULT 'not_approved',
  config JSONB NOT NULL DEFAULT '{}', updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS financial_scenarios (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, assumptions JSONB NOT NULL DEFAULT '{}',
  results JSONB NOT NULL DEFAULT '{}', created_by TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_conversation_preferences_actor
  ON conversation_preferences(actor_kind, actor_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_legal_review ON provider_legal_profiles(review_status, updated_at);
CREATE INDEX IF NOT EXISTS idx_org_owner ON customer_organizations(owner_user_id, status);
CREATE INDEX IF NOT EXISTS idx_contract_owner ON maintenance_contracts(owner_user_id, status, next_due_at);
CREATE INDEX IF NOT EXISTS idx_contract_provider ON maintenance_contracts(provider_id, status, next_due_at);
CREATE INDEX IF NOT EXISTS idx_crm_provider ON provider_crm_records(provider_id, stage, updated_at);
CREATE INDEX IF NOT EXISTS idx_training_provider ON training_progress(provider_id, status);
CREATE INDEX IF NOT EXISTS idx_demand_alert ON demand_alerts(service_value, gov, wilayah, status);
CREATE INDEX IF NOT EXISTS idx_risk_queue ON risk_reviews(status, score, created_at);
