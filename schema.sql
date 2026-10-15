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

-- Supabase exposes public tables through its REST API using the public "anon"
-- key. This app connects directly to Postgres instead, so lock the API out.
alter table tanks        enable row level security;
alter table readings     enable row level security;
alter table calibrations enable row level security;
alter table events       enable row level security;
alter table lab_results  enable row level security;
alter table alerts       enable row level security;
