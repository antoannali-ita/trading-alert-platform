-- Trading Alert Platform foundation
-- Spec v1.2 FINAL

create extension if not exists pgcrypto;

create schema if not exists alert_platform;

-- ---------- enums ----------
create type alert_platform.alert_status as enum (
  'ACTIVE','CLAIMED','TRIGGERED','V3_PENDING','V3_RUNNING','V3_RETRY',
  'V3_COMPLETED','V3_FAILED','PROCESSED','EXPIRED','CANCELLED'
);

create type alert_platform.alert_type as enum (
  'PRICE_BELOW','PRICE_ABOVE','ENTRY_ZONE','MAX_BUY',
  'BREAKOUT','PULLBACK','SUPPORT','RESISTANCE','CUSTOM'
);

create type alert_platform.trigger_confirmation as enum (
  'INSTANT','TWO_CHECKS','CLOSE_5M','CLOSE_15M'
);

create type alert_platform.notification_policy as enum (
  'NONE','BUY_ONLY','BUY_PREBUY_HIGH'
);

create type alert_platform.notification_type as enum (
  'WHATSAPP','EMAIL','PUSH','WEBHOOK'
);

create type alert_platform.notification_status as enum (
  'PENDING','SENT','FAILED'
);

create type alert_platform.v3_status as enum (
  'V3_PENDING','V3_RUNNING','V3_RETRY','V3_COMPLETED','V3_FAILED'
);

create type alert_platform.v3_decision as enum (
  'BUY_NOW','BUY_LIMIT','PRE_BUY_HIGH','PRE_BUY','WAIT','WATCH','AVOID','DATA_REVIEW'
);

-- ---------- config ----------
create table alert_platform.system_config (
  key text primary key,
  value text not null,
  updated_at timestamptz not null default now()
);

insert into alert_platform.system_config(key,value) values
  ('claim_lease_seconds','180'),
  ('heartbeat_threshold_seconds','60'),
  ('distance_near','0.02'),
  ('distance_medium','0.05'),
  ('check_near_minutes','5'),
  ('check_medium_minutes','15'),
  ('check_far_minutes','30'),
  ('market_data_max_requests_per_minute','8'),
  ('market_data_max_symbols_per_batch','8'),
  ('max_price_age_seconds','120'),
  ('v3_retry_1_minutes','2'),
  ('v3_retry_2_minutes','10'),
  ('v3_retry_3_minutes','30'),
  ('v3_snapshot_fresh_days','7'),
  ('v3_snapshot_stale_days','14'),
  ('default_notification_policy','BUY_PREBUY_HIGH')
on conflict (key) do nothing;

create or replace function alert_platform.get_config_int(p_key text, p_default integer default null)
returns integer
language plpgsql
stable
set search_path = alert_platform, pg_temp
as $$
declare v_value text;
begin
  select value into v_value from alert_platform.system_config where key = p_key;
  if v_value is null then return p_default; end if;
  begin
    return v_value::integer;
  exception when invalid_text_representation then
    raise exception 'Invalid integer config: % = %', p_key, v_value;
  end;
end;
$$;

create or replace function alert_platform.get_config_numeric(p_key text, p_default numeric default null)
returns numeric
language plpgsql
stable
set search_path = alert_platform, pg_temp
as $$
declare v_value text;
begin
  select value into v_value from alert_platform.system_config where key = p_key;
  if v_value is null then return p_default; end if;
  begin
    return v_value::numeric;
  exception when invalid_text_representation then
    raise exception 'Invalid numeric config: % = %', p_key, v_value;
  end;
end;
$$;

create or replace function alert_platform.get_config_text(p_key text, p_default text default null)
returns text
language sql
stable
set search_path = alert_platform, pg_temp
as $$
  select coalesce((select value from alert_platform.system_config where key = p_key), p_default)
$$;

create or replace function alert_platform.validate_system_config()
returns trigger
language plpgsql
set search_path = alert_platform, pg_temp
as $$
declare
  v_num numeric;
  v_int integer;
begin
  if new.key in ('claim_lease_seconds','heartbeat_threshold_seconds','check_near_minutes',
                 'check_medium_minutes','check_far_minutes','market_data_max_requests_per_minute',
                 'market_data_max_symbols_per_batch','max_price_age_seconds','v3_retry_1_minutes',
                 'v3_retry_2_minutes','v3_retry_3_minutes','v3_snapshot_fresh_days','v3_snapshot_stale_days') then
    begin v_int := new.value::integer;
    exception when invalid_text_representation then
      raise exception 'Invalid integer config: % = %', new.key, new.value;
    end;
  end if;

  if new.key in ('distance_near','distance_medium') then
    begin v_num := new.value::numeric;
    exception when invalid_text_representation then
      raise exception 'Invalid numeric config: % = %', new.key, new.value;
    end;
  end if;

  if new.key = 'claim_lease_seconds' and v_int <= 0 then raise exception 'claim_lease_seconds must be > 0'; end if;
  if new.key = 'heartbeat_threshold_seconds' and v_int < 0 then raise exception 'heartbeat_threshold_seconds must be >= 0'; end if;
  if new.key like 'check_%_minutes' and v_int <= 0 then raise exception '% must be > 0', new.key; end if;
  if new.key in ('market_data_max_requests_per_minute','market_data_max_symbols_per_batch','max_price_age_seconds') and v_int <= 0 then raise exception '% must be > 0', new.key; end if;
  if new.key like 'v3_retry_%_minutes' and v_int < 0 then raise exception '% must be >= 0', new.key; end if;
  if new.key in ('v3_snapshot_fresh_days','v3_snapshot_stale_days') and v_int < 0 then raise exception '% must be >= 0', new.key; end if;
  if new.key in ('distance_near','distance_medium') and v_num <= 0 then raise exception '% must be > 0', new.key; end if;

  new.updated_at := now();
  return new;
end;
$$;

create trigger trg_validate_system_config
before insert or update on alert_platform.system_config
for each row execute function alert_platform.validate_system_config();

-- ---------- core tables ----------
create table alert_platform.alerts (
  id uuid primary key default gen_random_uuid(),
  ticker text not null,
  market text not null,
  alert_type alert_platform.alert_type not null,
  condition text,
  threshold numeric,
  threshold_min numeric,
  threshold_max numeric,
  status alert_platform.alert_status not null default 'ACTIVE',
  priority integer not null default 50 check (priority between 0 and 100),
  trigger_mode text not null default 'ONE_SHOT' check (trigger_mode = 'ONE_SHOT'),
  trigger_confirmation alert_platform.trigger_confirmation not null default 'INSTANT',
  notification_policy alert_platform.notification_policy not null default 'BUY_PREBUY_HIGH',
  valid_until timestamptz not null,
  next_check_at timestamptz not null default now(),
  last_price numeric,
  last_price_at timestamptz,
  last_price_provider text,
  triggered_at timestamptz,
  trigger_price numeric,
  trigger_event_id uuid,
  claimed_at timestamptz,
  claimed_by uuid,
  claim_expires_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  processed_at timestamptz,
  constraint chk_alert_mvp_contract check (
    (alert_type in ('PRICE_BELOW','PRICE_ABOVE','MAX_BUY') and threshold is not null and threshold_min is null and threshold_max is null)
    or
    (alert_type = 'ENTRY_ZONE' and threshold is null and threshold_min is not null and threshold_max is not null and threshold_min <= threshold_max)
    or
    (alert_type in ('BREAKOUT','PULLBACK','SUPPORT','RESISTANCE','CUSTOM'))
  ),
  constraint chk_valid_until_after_created check (valid_until > created_at)
);

create table alert_platform.alert_sources (
  id uuid primary key default gen_random_uuid(),
  alert_id uuid not null references alert_platform.alerts(id) on delete cascade,
  source text not null,
  strategy text,
  original_condition text,
  original_threshold numeric,
  original_threshold_min numeric,
  original_threshold_max numeric,
  score numeric,
  notification_policy alert_platform.notification_policy,
  metadata jsonb,
  created_at timestamptz not null default now()
);

create table alert_platform.trigger_events (
  id uuid primary key default gen_random_uuid(),
  trigger_event_id uuid not null unique default gen_random_uuid(),
  ticker text not null,
  market text not null,
  trigger_price numeric not null,
  trigger_price_at timestamptz not null,
  provider text,
  effective_notification_policy alert_platform.notification_policy not null,
  created_at timestamptz not null default now()
);

alter table alert_platform.alerts
  add constraint fk_alerts_trigger_event
  foreign key (trigger_event_id) references alert_platform.trigger_events(trigger_event_id);

create table alert_platform.trigger_event_alerts (
  trigger_event_id uuid not null references alert_platform.trigger_events(trigger_event_id) on delete cascade,
  alert_id uuid not null references alert_platform.alerts(id),
  primary key (trigger_event_id, alert_id)
);

create table alert_platform.alert_runs (
  id uuid primary key default gen_random_uuid(),
  worker_id uuid not null,
  alert_id uuid references alert_platform.alerts(id),
  ticker text not null,
  checked_at timestamptz not null default now(),
  price numeric,
  price_timestamp timestamptz,
  provider text,
  trigger_hit boolean,
  trigger_event_id uuid references alert_platform.trigger_events(trigger_event_id),
  error_code text,
  retry_count integer not null default 0,
  duration_ms integer
);

create table alert_platform.v3_runs (
  id uuid primary key default gen_random_uuid(),
  v3_run_id uuid not null unique default gen_random_uuid(),
  trigger_event_id uuid not null references alert_platform.trigger_events(trigger_event_id),
  ticker text not null,
  status alert_platform.v3_status not null,
  retry_number integer not null default 0,
  manual_retry boolean not null default false,
  started_at timestamptz,
  finished_at timestamptz,
  decision alert_platform.v3_decision,
  score numeric,
  entry numeric,
  buy_min numeric,
  buy_max numeric,
  max_buy numeric,
  stop numeric,
  tp1 numeric,
  tp2 numeric,
  rr_gross_tp1 numeric,
  rr_gross_tp2 numeric,
  rr_net_tp1 numeric,
  rr_net_tp2 numeric,
  qty integer,
  data_quality text,
  error_code text,
  payload jsonb,
  created_at timestamptz not null default now()
);

create table alert_platform.notifications (
  id uuid primary key default gen_random_uuid(),
  trigger_event_id uuid not null references alert_platform.trigger_events(trigger_event_id),
  ticker text not null,
  notification_type alert_platform.notification_type not null,
  status alert_platform.notification_status not null default 'PENDING',
  provider_message_id text,
  payload_hash text,
  error_code text,
  created_at timestamptz not null default now(),
  sent_at timestamptz,
  unique(trigger_event_id, notification_type)
);

create table alert_platform.alert_history (
  id uuid primary key default gen_random_uuid(),
  alert_id uuid not null references alert_platform.alerts(id),
  event_type text not null,
  old_status alert_platform.alert_status,
  new_status alert_platform.alert_status,
  trigger_event_id uuid references alert_platform.trigger_events(trigger_event_id),
  worker_id uuid,
  payload jsonb,
  created_at timestamptz not null default now()
);

-- ---------- updated_at ----------
create or replace function alert_platform.set_updated_at()
returns trigger
language plpgsql
set search_path = alert_platform, pg_temp
as $$
begin
  new.updated_at := now();
  return new;
end;
$$;

create trigger trg_alerts_updated_at
before update on alert_platform.alerts
for each row execute function alert_platform.set_updated_at();

-- ---------- indexes ----------
create index idx_alerts_due on alert_platform.alerts(status, next_check_at);
create index idx_alerts_ticker_status on alert_platform.alerts(ticker, status);
create index idx_alerts_valid_until on alert_platform.alerts(valid_until);
create index idx_alerts_claim_expiry on alert_platform.alerts(claim_expires_at);
create index idx_alerts_trigger_event on alert_platform.alerts(trigger_event_id);
create index idx_alert_runs_alert_time on alert_platform.alert_runs(alert_id, checked_at);
create index idx_alert_runs_worker on alert_platform.alert_runs(worker_id);
create index idx_alert_runs_trigger on alert_platform.alert_runs(trigger_event_id);
create index idx_v3_runs_trigger on alert_platform.v3_runs(trigger_event_id);
create index idx_notifications_trigger on alert_platform.notifications(trigger_event_id);
create index idx_notifications_status on alert_platform.notifications(status);

-- ---------- RLS foundation ----------
alter table alert_platform.system_config enable row level security;
alter table alert_platform.alerts enable row level security;
alter table alert_platform.alert_sources enable row level security;
alter table alert_platform.trigger_events enable row level security;
alter table alert_platform.trigger_event_alerts enable row level security;
alter table alert_platform.alert_runs enable row level security;
alter table alert_platform.v3_runs enable row level security;
alter table alert_platform.notifications enable row level security;
alter table alert_platform.alert_history enable row level security;

-- No anon/authenticated write policy is created in foundation.
-- Backend/service role owns state transitions; UI access policies come in a dedicated migration.

comment on schema alert_platform is 'Trading Alert Platform v1.2 FINAL foundation';
