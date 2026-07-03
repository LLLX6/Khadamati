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
  created_at TIMESTAMPTZ DEFAULT now()
);

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
  services JSONB NOT NULL,
  work_images JSONB DEFAULT '[]',
  documents JSONB DEFAULT '[]',
  quality_score INTEGER DEFAULT 60,
  response_score INTEGER DEFAULT 70,
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
  approved BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS complaints (
  id TEXT PRIMARY KEY,
  provider_id TEXT REFERENCES providers(id) ON DELETE SET NULL,
  customer_name TEXT,
  phone TEXT,
  reason TEXT,
  detail TEXT,
  status TEXT NOT NULL DEFAULT 'open',
  priority TEXT NOT NULL DEFAULT 'normal',
  resolution TEXT DEFAULT '',
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS packages (
  id TEXT PRIMARY KEY,
  ar TEXT NOT NULL,
  en TEXT NOT NULL,
  price NUMERIC NOT NULL DEFAULT 0,
  duration_days INTEGER NOT NULL DEFAULT 30,
  featured_boost INTEGER NOT NULL DEFAULT 0,
  max_services INTEGER NOT NULL DEFAULT 3,
  max_images INTEGER NOT NULL DEFAULT 5,
  active BOOLEAN NOT NULL DEFAULT TRUE
);

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
  used_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT now()
);

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
