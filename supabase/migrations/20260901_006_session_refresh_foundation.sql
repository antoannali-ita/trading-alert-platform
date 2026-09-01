-- Session Refresh V1 foundation.
-- Adds persistent session claim/lease state, per-ticker refresh tracking,
-- provider retry persistence helpers, and frozen config defaults.

create table if not exists alert_platform.market_session_state (
  id uuid primary key default gen_random_uuid(),
  market text not null,
  session_date date not null,
  opened_at timestamptz not null,
  refresh_due_at timestamptz not null,
  status text not null default 'PENDING'
    check (status in ('PENDING','CLAIMED','COMPLETED','COMPLETED_WITH_PENDING','FAILED')),
  claimed_by uuid,
  claimed_at timestamptz,
  claim_expires_at timestamptz,
  heartbeat_at timestamptz,
  refresh_started_at timestamptz,
  refresh_completed_at timestamptz,
  result jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (market, session_date),
  check (refresh_due_at >= opened_at)
);

create table if not exists alert_platform.session_refresh_items (
  id uuid primary key default gen_random_uuid(),
  session_state_id uuid not null references alert_platform.market_session_state(id) on delete cascade,
  ticker text not null,
  market text not null,
  priority_class text not null default 'NORMAL'
    check (priority_class in ('CRITICAL','HIGH','NORMAL','LOW')),
  status text not null default 'PENDING_REFRESH'
    check (status in ('PENDING_REFRESH','PENDING_OPEN','PROCESSING','UPDATED','DEGRADED','NO_OPEN_DATA','FAILED')),
  retry_count integer not null default 0 check (retry_count >= 0),
  next_retry_at timestamptz,
  current_price numeric,
  price_timestamp timestamptz,
  open_price numeric,
  previous_close numeric,
  high_price numeric,
  low_price numeric,
  volume numeric,
  provider text,
  data_quality text,
  distance_to_trigger numeric,
  gap_pct numeric,
  gap_flags text[],
  processed_at timestamptz,
  error_code text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (session_state_id, ticker)
);

create index if not exists idx_market_session_due
  on alert_platform.market_session_state(status, refresh_due_at);
create index if not exists idx_session_refresh_queue
  on alert_platform.session_refresh_items(session_state_id, status, priority_class, next_retry_at);
create index if not exists idx_alert_runs_retry
  on alert_platform.alert_runs(alert_id, checked_at desc, retry_count desc);

insert into alert_platform.system_config(key, value) values
  ('session_refresh_delay_minutes','3'),
  ('session_refresh_max_duration_minutes','15'),
  ('pending_open_retry_1_minutes','2'),
  ('pending_open_retry_2_minutes','5'),
  ('provider_retry_1_minutes','1'),
  ('provider_retry_2_minutes','5'),
  ('gap_minor_pct','0.01'),
  ('gap_material_pct','0.02'),
  ('gap_large_pct','0.03'),
  ('gap_extreme_pct','0.05'),
  ('twelve_max_requests_per_minute','8'),
  ('twelve_daily_budget','800'),
  ('twelve_warning_threshold','0.80'),
  ('fallback_enabled','true')
on conflict (key) do nothing;

create or replace function alert_platform.ensure_market_session(
  p_market text,
  p_session_date date,
  p_opened_at timestamptz,
  p_refresh_due_at timestamptz
)
returns alert_platform.market_session_state
language plpgsql
security definer
set search_path = alert_platform, pg_temp
as $$
declare
  v_row alert_platform.market_session_state;
begin
  insert into alert_platform.market_session_state(market, session_date, opened_at, refresh_due_at)
  values (upper(p_market), p_session_date, p_opened_at, p_refresh_due_at)
  on conflict (market, session_date) do update
    set opened_at = excluded.opened_at,
        refresh_due_at = excluded.refresh_due_at,
        updated_at = now()
  returning * into v_row;
  return v_row;
end;
$$;

create or replace function alert_platform.claim_session_refresh(
  p_market text,
  p_session_date date,
  p_worker_id uuid
)
returns setof alert_platform.market_session_state
language plpgsql
security definer
set search_path = alert_platform, pg_temp
as $$
declare
  v_lease_seconds integer := alert_platform.get_config_int('claim_lease_seconds', 180);
begin
  return query
  update alert_platform.market_session_state s
     set status = 'CLAIMED',
         claimed_by = p_worker_id,
         claimed_at = now(),
         claim_expires_at = now() + make_interval(secs => v_lease_seconds),
         heartbeat_at = now(),
         refresh_started_at = coalesce(s.refresh_started_at, now()),
         updated_at = now()
   where s.market = upper(p_market)
     and s.session_date = p_session_date
     and s.refresh_due_at <= now()
     and (
       s.status = 'PENDING'
       or (s.status = 'CLAIMED' and s.claim_expires_at < now())
     )
  returning s.*;
end;
$$;

create or replace function alert_platform.heartbeat_session_refresh(
  p_session_id uuid,
  p_worker_id uuid
)
returns boolean
language plpgsql
security definer
set search_path = alert_platform, pg_temp
as $$
declare
  v_lease_seconds integer := alert_platform.get_config_int('claim_lease_seconds', 180);
  v_count integer;
begin
  update alert_platform.market_session_state
     set heartbeat_at = now(),
         claim_expires_at = now() + make_interval(secs => v_lease_seconds),
         updated_at = now()
   where id = p_session_id
     and status = 'CLAIMED'
     and claimed_by = p_worker_id;
  get diagnostics v_count = row_count;
  return v_count = 1;
end;
$$;

create or replace function alert_platform.complete_session_refresh(
  p_session_id uuid,
  p_worker_id uuid,
  p_result jsonb default '{}'::jsonb
)
returns boolean
language plpgsql
security definer
set search_path = alert_platform, pg_temp
as $$
declare
  v_pending integer;
  v_count integer;
begin
  if not exists (
    select 1 from alert_platform.market_session_state
     where id = p_session_id and status = 'CLAIMED' and claimed_by = p_worker_id
  ) then
    return false;
  end if;

  select count(*) into v_pending
    from alert_platform.session_refresh_items
   where session_state_id = p_session_id
     and status in ('PENDING_REFRESH','PENDING_OPEN','PROCESSING');

  update alert_platform.market_session_state
     set status = case when v_pending > 0 then 'COMPLETED_WITH_PENDING' else 'COMPLETED' end,
         refresh_completed_at = now(),
         result = coalesce(p_result, '{}'::jsonb) || jsonb_build_object('pending_items', v_pending),
         claim_expires_at = null,
         claimed_by = null,
         heartbeat_at = now(),
         updated_at = now()
   where id = p_session_id
     and status = 'CLAIMED'
     and claimed_by = p_worker_id;
  get diagnostics v_count = row_count;
  return v_count = 1;
end;
$$;

create or replace function alert_platform.expire_session_refresh_if_needed(
  p_session_id uuid,
  p_worker_id uuid
)
returns boolean
language plpgsql
security definer
set search_path = alert_platform, pg_temp
as $$
declare
  v_max_minutes integer := alert_platform.get_config_int('session_refresh_max_duration_minutes', 15);
  v_count integer;
begin
  update alert_platform.session_refresh_items i
     set status = 'PENDING_REFRESH', updated_at = now()
   where i.session_state_id = p_session_id
     and i.status in ('PENDING_OPEN','PROCESSING');

  update alert_platform.market_session_state
     set status = 'COMPLETED_WITH_PENDING',
         refresh_completed_at = now(),
         result = coalesce(result, '{}'::jsonb) || jsonb_build_object('timeout', true),
         claimed_by = null,
         claim_expires_at = null,
         updated_at = now()
   where id = p_session_id
     and status = 'CLAIMED'
     and claimed_by = p_worker_id
     and refresh_started_at + make_interval(mins => v_max_minutes) <= now();
  get diagnostics v_count = row_count;
  return v_count = 1;
end;
$$;

create or replace function alert_platform.record_alert_run(
  p_worker_id uuid,
  p_alert_id uuid,
  p_ticker text,
  p_price numeric default null,
  p_price_timestamp timestamptz default null,
  p_provider text default null,
  p_trigger_hit boolean default null,
  p_error_code text default null,
  p_duration_ms integer default null
)
returns alert_platform.alert_runs
language plpgsql
security definer
set search_path = alert_platform, pg_temp
as $$
declare
  v_retry integer := 0;
  v_row alert_platform.alert_runs;
begin
  if p_error_code is not null then
    select coalesce(max(retry_count), -1) + 1 into v_retry
      from alert_platform.alert_runs
     where alert_id = p_alert_id
       and error_code is not null
       and checked_at >= now() - interval '1 day';
  end if;

  insert into alert_platform.alert_runs(
    worker_id, alert_id, ticker, price, price_timestamp, provider,
    trigger_hit, error_code, retry_count, duration_ms
  ) values (
    p_worker_id, p_alert_id, upper(p_ticker), p_price, p_price_timestamp, p_provider,
    p_trigger_hit, p_error_code, v_retry, p_duration_ms
  ) returning * into v_row;
  return v_row;
end;
$$;

alter table alert_platform.market_session_state enable row level security;
alter table alert_platform.session_refresh_items enable row level security;

revoke all on alert_platform.market_session_state from anon, authenticated;
revoke all on alert_platform.session_refresh_items from anon, authenticated;
revoke execute on function alert_platform.ensure_market_session(text,date,timestamptz,timestamptz) from anon, authenticated;
revoke execute on function alert_platform.claim_session_refresh(text,date,uuid) from anon, authenticated;
revoke execute on function alert_platform.heartbeat_session_refresh(uuid,uuid) from anon, authenticated;
revoke execute on function alert_platform.complete_session_refresh(uuid,uuid,jsonb) from anon, authenticated;
revoke execute on function alert_platform.expire_session_refresh_if_needed(uuid,uuid) from anon, authenticated;
revoke execute on function alert_platform.record_alert_run(uuid,uuid,text,numeric,timestamptz,text,boolean,text,integer) from anon, authenticated;

grant execute on function alert_platform.ensure_market_session(text,date,timestamptz,timestamptz) to service_role;
grant execute on function alert_platform.claim_session_refresh(text,date,uuid) to service_role;
grant execute on function alert_platform.heartbeat_session_refresh(uuid,uuid) to service_role;
grant execute on function alert_platform.complete_session_refresh(uuid,uuid,jsonb) to service_role;
grant execute on function alert_platform.expire_session_refresh_if_needed(uuid,uuid) to service_role;
grant execute on function alert_platform.record_alert_run(uuid,uuid,text,numeric,timestamptz,text,boolean,text,integer) to service_role;
