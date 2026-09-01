-- Trading Alert Platform atomic RPCs
-- Depends on 20260901_001_alert_platform_foundation.sql

-- Claim due alerts atomically using SKIP LOCKED.
create or replace function alert_platform.claim_due_alerts(
  p_worker_id uuid,
  p_limit integer
)
returns setof alert_platform.alerts
language plpgsql
security definer
set search_path = alert_platform, pg_temp
as $$
declare
  v_lease_seconds integer := alert_platform.get_config_int('claim_lease_seconds', 180);
begin
  if p_worker_id is null then
    raise exception 'p_worker_id is required';
  end if;
  if p_limit is null or p_limit <= 0 then
    raise exception 'p_limit must be > 0';
  end if;

  return query
  with candidates as (
    select a.id
    from alert_platform.alerts a
    where a.status = 'ACTIVE'
      and a.next_check_at <= now()
      and a.valid_until >= now()
    order by a.priority desc, a.next_check_at asc, a.created_at asc
    for update skip locked
    limit p_limit
  )
  update alert_platform.alerts a
     set status = 'CLAIMED',
         claimed_at = now(),
         claimed_by = p_worker_id,
         claim_expires_at = now() + make_interval(secs => v_lease_seconds)
    from candidates c
   where a.id = c.id
  returning a.*;
end;
$$;

-- Heartbeat only while worker still owns a live claim.
create or replace function alert_platform.extend_claim(
  p_alert_id uuid,
  p_worker_id uuid
)
returns boolean
language plpgsql
security definer
set search_path = alert_platform, pg_temp
as $$
declare
  v_lease_seconds integer := alert_platform.get_config_int('claim_lease_seconds', 180);
  v_threshold integer := alert_platform.get_config_int('heartbeat_threshold_seconds', 60);
  v_updated integer;
begin
  update alert_platform.alerts
     set claim_expires_at = now() + make_interval(secs => v_lease_seconds)
   where id = p_alert_id
     and status = 'CLAIMED'
     and claimed_by = p_worker_id
     and claim_expires_at is not null
     and claim_expires_at - now() < make_interval(secs => v_threshold);

  get diagnostics v_updated = row_count;
  return v_updated > 0;
end;
$$;

-- Release an owned claim back to ACTIVE after a valid non-trigger check.
create or replace function alert_platform.release_alert(
  p_alert_id uuid,
  p_worker_id uuid,
  p_next_check_at timestamptz,
  p_price numeric,
  p_price_at timestamptz,
  p_provider text
)
returns boolean
language plpgsql
security definer
set search_path = alert_platform, pg_temp
as $$
declare
  v_updated integer;
begin
  update alert_platform.alerts
     set status = 'ACTIVE',
         next_check_at = p_next_check_at,
         claimed_at = null,
         claimed_by = null,
         claim_expires_at = null,
         last_price = p_price,
         last_price_at = p_price_at,
         last_price_provider = p_provider
   where id = p_alert_id
     and status = 'CLAIMED'
     and claimed_by = p_worker_id;

  get diagnostics v_updated = row_count;
  return v_updated > 0;
end;
$$;

-- Mark an owned claimed alert expired if its validity window has elapsed.
create or replace function alert_platform.expire_alert(
  p_alert_id uuid,
  p_worker_id uuid
)
returns boolean
language plpgsql
security definer
set search_path = alert_platform, pg_temp
as $$
declare
  v_updated integer;
begin
  update alert_platform.alerts
     set status = 'EXPIRED',
         processed_at = now(),
         claimed_at = null,
         claimed_by = null,
         claim_expires_at = null,
         next_check_at = null
   where id = p_alert_id
     and status = 'CLAIMED'
     and claimed_by = p_worker_id
     and valid_until < now();

  get diagnostics v_updated = row_count;
  return v_updated > 0;
end;
$$;

-- Create one consolidated trigger event for N alerts on one ticker/cycle.
-- Ownership is re-checked inside the transaction. If any alert is no longer owned,
-- the function raises and no trigger_event is created.
create or replace function alert_platform.create_trigger_event(
  p_worker_id uuid,
  p_alert_ids uuid[],
  p_ticker text,
  p_market text,
  p_trigger_price numeric,
  p_trigger_price_at timestamptz,
  p_provider text,
  p_effective_policy alert_platform.notification_policy
)
returns uuid
language plpgsql
security definer
set search_path = alert_platform, pg_temp
as $$
declare
  v_event_id uuid := gen_random_uuid();
  v_expected integer;
  v_owned integer;
begin
  if p_alert_ids is null or cardinality(p_alert_ids) = 0 then
    raise exception 'p_alert_ids must not be empty';
  end if;

  v_expected := cardinality(p_alert_ids);

  select count(*) into v_owned
  from alert_platform.alerts a
  where a.id = any(p_alert_ids)
    and a.status = 'CLAIMED'
    and a.claimed_by = p_worker_id
    and a.valid_until >= p_trigger_price_at;

  if v_owned <> v_expected then
    raise exception 'LOCK_ERROR: expected % owned alerts, found %', v_expected, v_owned;
  end if;

  insert into alert_platform.trigger_events(
    trigger_event_id, ticker, market, trigger_price, trigger_price_at,
    provider, effective_notification_policy
  ) values (
    v_event_id, p_ticker, p_market, p_trigger_price, p_trigger_price_at,
    p_provider, p_effective_policy
  );

  insert into alert_platform.trigger_event_alerts(trigger_event_id, alert_id)
  select v_event_id, unnest(p_alert_ids);

  update alert_platform.alerts
     set status = 'TRIGGERED',
         triggered_at = p_trigger_price_at,
         trigger_price = p_trigger_price,
         trigger_event_id = v_event_id,
         next_check_at = null,
         claimed_at = null,
         claimed_by = null,
         claim_expires_at = null
   where id = any(p_alert_ids)
     and status = 'CLAIMED'
     and claimed_by = p_worker_id;

  if not found then
    raise exception 'LOCK_ERROR: trigger update affected no rows';
  end if;

  return v_event_id;
end;
$$;

-- Anti-race cancel: no forced cancellation of a live claim.
create or replace function alert_platform.cancel_alert(p_alert_id uuid)
returns boolean
language plpgsql
security definer
set search_path = alert_platform, pg_temp
as $$
declare
  v_updated integer;
begin
  update alert_platform.alerts
     set status = 'CANCELLED',
         processed_at = now(),
         next_check_at = null,
         claimed_at = null,
         claimed_by = null,
         claim_expires_at = null
   where id = p_alert_id
     and (
       status = 'ACTIVE'
       or (status = 'CLAIMED' and claim_expires_at < now())
     );

  get diagnostics v_updated = row_count;
  return v_updated > 0;
end;
$$;

-- Manual V3 retry: preserves trigger_event_id and creates a fresh v3_run_id.
create or replace function alert_platform.retry_v3(p_trigger_event_id uuid)
returns uuid
language plpgsql
security definer
set search_path = alert_platform, pg_temp
as $$
declare
  v_run_id uuid := gen_random_uuid();
  v_exists boolean;
begin
  select exists(
    select 1 from alert_platform.trigger_events
    where trigger_event_id = p_trigger_event_id
  ) into v_exists;

  if not v_exists then
    raise exception 'Unknown trigger_event_id: %', p_trigger_event_id;
  end if;

  if not exists (
    select 1
    from alert_platform.v3_runs
    where trigger_event_id = p_trigger_event_id
      and status = 'V3_FAILED'
  ) then
    raise exception 'Trigger event is not in V3_FAILED state';
  end if;

  insert into alert_platform.v3_runs(
    v3_run_id, trigger_event_id, ticker, status, retry_number, manual_retry
  )
  select
    v_run_id,
    te.trigger_event_id,
    te.ticker,
    'V3_PENDING',
    coalesce((select max(v.retry_number) from alert_platform.v3_runs v where v.trigger_event_id = te.trigger_event_id), 0) + 1,
    true
  from alert_platform.trigger_events te
  where te.trigger_event_id = p_trigger_event_id;

  update alert_platform.alerts a
     set status = 'V3_PENDING'
   where a.id in (
     select tea.alert_id
     from alert_platform.trigger_event_alerts tea
     where tea.trigger_event_id = p_trigger_event_id
   );

  return v_run_id;
end;
$$;

-- Validated admin configuration write.
create or replace function alert_platform.set_system_config(p_key text, p_value text)
returns void
language plpgsql
security definer
set search_path = alert_platform, pg_temp
as $$
declare
  v_claim integer;
  v_heartbeat integer;
  v_near numeric;
  v_medium numeric;
  v_fresh integer;
  v_stale integer;
begin
  insert into alert_platform.system_config(key, value)
  values (p_key, p_value)
  on conflict (key) do update
    set value = excluded.value,
        updated_at = now();

  -- Cross-key invariants after row-level validation has succeeded.
  v_claim := alert_platform.get_config_int('claim_lease_seconds', 180);
  v_heartbeat := alert_platform.get_config_int('heartbeat_threshold_seconds', 60);
  v_near := alert_platform.get_config_numeric('distance_near', 0.02);
  v_medium := alert_platform.get_config_numeric('distance_medium', 0.05);
  v_fresh := alert_platform.get_config_int('v3_snapshot_fresh_days', 7);
  v_stale := alert_platform.get_config_int('v3_snapshot_stale_days', 14);

  if v_heartbeat >= v_claim then
    raise exception 'heartbeat_threshold_seconds must be < claim_lease_seconds';
  end if;
  if v_medium <= v_near then
    raise exception 'distance_medium must be > distance_near';
  end if;
  if v_stale <= v_fresh then
    raise exception 'v3_snapshot_stale_days must be > v3_snapshot_fresh_days';
  end if;
end;
$$;

-- Explicit grants will be narrowed once the service/auth roles used by deployment are wired.
revoke all on function alert_platform.claim_due_alerts(uuid, integer) from public;
revoke all on function alert_platform.extend_claim(uuid, uuid) from public;
revoke all on function alert_platform.release_alert(uuid, uuid, timestamptz, numeric, timestamptz, text) from public;
revoke all on function alert_platform.expire_alert(uuid, uuid) from public;
revoke all on function alert_platform.create_trigger_event(uuid, uuid[], text, text, numeric, timestamptz, text, alert_platform.notification_policy) from public;
revoke all on function alert_platform.cancel_alert(uuid) from public;
revoke all on function alert_platform.retry_v3(uuid) from public;
revoke all on function alert_platform.set_system_config(text, text) from public;
