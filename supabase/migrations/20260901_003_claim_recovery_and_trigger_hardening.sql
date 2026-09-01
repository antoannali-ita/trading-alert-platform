-- Claim recovery + trigger hardening
-- Spec v1.2 FINAL

-- Expired CLAIMED rows must be recoverable. Reclaim them directly in the same
-- atomic candidate selection used for normal ACTIVE alerts.
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
    where (
        (a.status = 'ACTIVE' and a.next_check_at <= now())
        or
        (a.status = 'CLAIMED' and a.claim_expires_at < now())
      )
      and a.valid_until >= now()
    order by
      case when a.status = 'CLAIMED' then 0 else 1 end,
      a.priority desc,
      a.next_check_at asc nulls first,
      a.created_at asc
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

-- Harden consolidated trigger creation:
-- 1. reject duplicate alert ids;
-- 2. lock every target alert FOR UPDATE;
-- 3. verify full ownership while locked;
-- 4. require UPDATE rowcount == expected count.
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
  v_distinct integer;
  v_owned integer;
  v_updated integer;
begin
  if p_worker_id is null then
    raise exception 'p_worker_id is required';
  end if;
  if p_alert_ids is null or cardinality(p_alert_ids) = 0 then
    raise exception 'p_alert_ids must not be empty';
  end if;
  if p_trigger_price is null or p_trigger_price_at is null then
    raise exception 'trigger price and timestamp are required';
  end if;

  v_expected := cardinality(p_alert_ids);
  select count(distinct x) into v_distinct from unnest(p_alert_ids) as t(x);
  if v_distinct <> v_expected then
    raise exception 'p_alert_ids contains duplicates';
  end if;

  -- Lock all candidate rows so ownership cannot change between validation and update.
  perform 1
    from alert_platform.alerts a
   where a.id = any(p_alert_ids)
   order by a.id
   for update;

  select count(*) into v_owned
    from alert_platform.alerts a
   where a.id = any(p_alert_ids)
     and a.status = 'CLAIMED'
     and a.claimed_by = p_worker_id
     and a.claim_expires_at >= now()
     and a.valid_until >= p_trigger_price_at;

  if v_owned <> v_expected then
    raise exception 'LOCK_ERROR: expected % live owned alerts, found %', v_expected, v_owned;
  end if;

  insert into alert_platform.trigger_events(
    trigger_event_id, ticker, market, trigger_price, trigger_price_at,
    provider, effective_notification_policy
  ) values (
    v_event_id, p_ticker, p_market, p_trigger_price, p_trigger_price_at,
    p_provider, p_effective_policy
  );

  insert into alert_platform.trigger_event_alerts(trigger_event_id, alert_id)
  select v_event_id, x from unnest(p_alert_ids) as t(x);

  update alert_platform.alerts a
     set status = 'TRIGGERED',
         triggered_at = p_trigger_price_at,
         trigger_price = p_trigger_price,
         trigger_event_id = v_event_id,
         next_check_at = null,
         claimed_at = null,
         claimed_by = null,
         claim_expires_at = null
   where a.id = any(p_alert_ids)
     and a.status = 'CLAIMED'
     and a.claimed_by = p_worker_id;

  get diagnostics v_updated = row_count;
  if v_updated <> v_expected then
    raise exception 'LOCK_ERROR: expected to trigger % alerts, updated %', v_expected, v_updated;
  end if;

  return v_event_id;
end;
$$;

revoke all on function alert_platform.claim_due_alerts(uuid, integer) from public;
revoke all on function alert_platform.create_trigger_event(uuid, uuid[], text, text, numeric, timestamptz, text, alert_platform.notification_policy) from public;
