-- Run once with:  flask --app app init-db
-- Safe to re-run (everything is "if not exists").

create table if not exists tanks (
  device_id  text primary key,
  name       text not null,
  dataset    text not null check (dataset in ('demo', 'pilot')),
  created_at timestamptz not null default now()
);

-- One row per device message. Raw millivolts are always kept so calibration
-- can be re-applied later. A sensor that is not wired is simply NULL.
create table if not exists readings (
  id                bigserial primary key,
  device_id         text not null references tanks(device_id) on delete cascade,
  ts                timestamptz not null,
  received_at       timestamptz not null default now(),
  ph                real,
  ph_raw_mv         real,
  turbidity_ntu     real,
  turbidity_raw_mv  real,
  tds_ppm           real,
  temp_c            real,
  orp_mv            real,
  fw                text,
  unique (device_id, ts)          -- a retried POST cannot create duplicates
);
create index if not exists readings_device_ts on readings (device_id, ts desc);

-- Calibration history: which coefficients were valid from when.
create table if not exists calibrations (
  id         bigserial primary key,
  device_id  text not null references tanks(device_id) on delete cascade,
  sensor     text not null,                 -- 'ph', 'turbidity', ...
  method     text not null,                 -- 'two_point', ...
  params     jsonb not null,                -- e.g. {"slope": -0.01, "offset": 32.0}
  valid_from timestamptz not null default now(),
  created_at timestamptz not null default now()
);

-- Things operators did that explain sensor changes (cleaning, refill, rain...).
create table if not exists events (
  id         bigserial primary key,
  device_id  text not null references tanks(device_id) on delete cascade,
  ts         timestamptz not null,
  kind       text not null check (kind in ('cleaning', 'refill', 'rain', 'maintenance', 'other')),
  note       text,
  created_at timestamptz not null default now()
);
create index if not exists events_device_ts on events (device_id, ts desc);

-- Manual lab reports: ground truth for tuning the detectors.
create table if not exists lab_results (
  id            bigserial primary key,
  device_id     text not null references tanks(device_id) on delete cascade,
  collected_at  timestamptz not null,
  ph            real,
  turbidity_ntu real,
  tds_ppm       real,
  bacteria      text not null default 'not_tested' check (bacteria in ('not_tested', 'absent', 'present')),
  note          text,
  created_at    timestamptz not null default now()
);

create table if not exists alerts (
  id             bigserial primary key,
  device_id      text not null references tanks(device_id) on delete cascade,
  created_at     timestamptz not null default now(),
  severity       text not null check (severity in ('yellow', 'red')),
  findings       jsonb not null,
  message        text not null,
  message_source text not null default 'template' check (message_source in ('template', 'llm')),
  status         text not null default 'open' check (status in ('open', 'closed')),
  feedback       text check (feedback in ('confirmed', 'false_alarm'))
);
create index if not exists alerts_device_created on alerts (device_id, created_at desc);

-- Tank profile and the optional public status page (operator-confirmed, see routes_web.py).
alter table tanks add column if not exists location          text;        -- where the tank is
alter table tanks add column if not exists probe_note        text;        -- probe depth, distance from inlet/outlet
alter table tanks add column if not exists public_enabled    boolean not null default false;
alter table tanks add column if not exists public_token      text unique; -- random, goes in the QR link
alter table tanks add column if not exists public_status     text not null default 'steady'
  check (public_status in ('steady', 'checking', 'tested'));
alter table tanks add column if not exists public_note       text;
alter table tanks add column if not exists public_updated_at timestamptz;

-- People who get SMS / WhatsApp messages when a pilot tank raises an alert.
create table if not exists contacts (
  id           bigserial primary key,
  dataset      text not null check (dataset in ('demo', 'pilot')),
  name         text not null,
  channel      text not null check (channel in ('sms', 'whatsapp')),
  phone        text not null,                       -- E.164, e.g. +919876543210
  min_severity text not null default 'yellow' check (min_severity in ('yellow', 'red')),
  created_at   timestamptz not null default now()
);

-- One row per message attempt, so a failed send is visible instead of silent.
create table if not exists notifications (
  id         bigserial primary key,
  alert_id   bigint references alerts(id) on delete cascade,
  contact_id bigint references contacts(id) on delete set null,
  channel    text not null,
  status     text not null check (status in ('sent', 'failed')),
  detail     text,
  created_at timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- Raw values for every sensor, so calibration can be re-applied later (outline section 8).
alter table readings add column if not exists tds_raw_mv real;
alter table readings add column if not exists orp_raw_mv real;

-- Firmware images for over-the-air updates. Stored in the database (not on disk) because
-- free web hosts have an ephemeral filesystem. Uploaded with `flask fw-release`, never over HTTP.
create table if not exists firmware_releases (
  version    text primary key,
  sha256     text not null,
  size_bytes integer not null,
  notes      text,
  image      bytea not null,
  created_at timestamptz not null default now()
);

-- One row per physical node: its own token (stored as a SHA-256 hash), what it last reported
-- about itself, and which firmware it should be running. Readings still belong to the tank.
create table if not exists devices (
  device_id    text primary key references tanks(device_id) on delete cascade,
  token_hash   text unique,                 -- null = the device still uses the shared DEVICE_TOKEN
  revoked      boolean not null default false,
  created_at   timestamptz not null default now(),
  last_seen_at timestamptz,                 -- any authenticated contact: reading, batch or check-in
  fw           text,
  rssi         integer,                     -- WiFi signal, dBm
  uptime_s     bigint,
  queued       integer,                     -- readings waiting in the device's own flash queue
  boot_reason  text,
  faults       jsonb not null default '[]', -- sensors the node says are failing, e.g. ["ph"]
  target_fw    text references firmware_releases(version) on delete set null,
  settings     jsonb not null default '{}'  -- per-device overrides of the periods sent back at check-in
);
-- Devices that existed before this table was added: seed them from their last reading.
insert into devices (device_id, last_seen_at)
  select t.device_id, max(r.received_at) from tanks t
  left join readings r on r.device_id = t.device_id
  where t.dataset = 'pilot' group by t.device_id
on conflict (device_id) do nothing;

-- Alerts about the device itself (went silent) are kept apart from water-change alerts.
alter table alerts add column if not exists kind text not null default 'water'
  check (kind in ('water', 'device'));
create index if not exists alerts_open_kind on alerts (device_id, kind) where status = 'open';

-- Lab results: one header row per sample, one row per measured parameter, optional report file.
alter table lab_results add column if not exists lab_name        text;
alter table lab_results add column if not exists sample_location text;   -- where in the tank it was taken
create table if not exists lab_values (
  id            bigserial primary key,
  lab_result_id bigint not null references lab_results(id) on delete cascade,
  parameter     text not null,              -- ph, turbidity, tds, free_chlorine, total_coliform, e_coli, ...
  qualifier     text not null default '=' check (qualifier in ('=', '<', '>', 'absent', 'present')),
  value         real,
  unit          text,
  check ((qualifier in ('absent', 'present') and value is null)
      or (qualifier in ('=', '<', '>') and value is not null))
);
create index if not exists lab_values_result on lab_values (lab_result_id);
create table if not exists lab_files (
  id            bigserial primary key,
  lab_result_id bigint not null references lab_results(id) on delete cascade,
  filename      text not null,
  content_type  text not null,
  size_bytes    integer not null,
  data          bytea not null,
  created_at    timestamptz not null default now()
);
create index if not exists lab_files_result on lab_files (lab_result_id);

-- Named operator accounts (the two env-var accounts keep working beside these).
create table if not exists users (
  id             bigserial primary key,
  username       text not null unique check (username = lower(username)),
  password_hash  text not null,
  dataset        text not null check (dataset in ('demo', 'pilot')),
  api_token_hash text unique,               -- SHA-256 of the user's API token, if one was issued
  disabled       boolean not null default false,
  created_at     timestamptz not null default now(),
  last_login_at  timestamptz
);

-- Supabase exposes public tables through its REST API using the public "anon"
-- key. This app connects directly to Postgres instead, so lock the API out.
alter table tanks        enable row level security;
alter table readings     enable row level security;
alter table calibrations enable row level security;
alter table events       enable row level security;
alter table lab_results  enable row level security;
alter table alerts       enable row level security;
alter table contacts      enable row level security;
alter table notifications enable row level security;
alter table firmware_releases enable row level security;
alter table devices          enable row level security;
alter table lab_values       enable row level security;
alter table lab_files        enable row level security;
alter table users            enable row level security;
