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
  pin_hash TEXT DEFAULT '',
  services JSONB NOT NULL,
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
