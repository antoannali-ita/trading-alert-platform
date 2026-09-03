-- Production cutover: permissions, queue repair and legacy ACTIVE-rule recovery.
begin;

grant select, insert, update on all tables in schema alert_platform to service_role;
alter default privileges in schema alert_platform
  grant select, insert, update on tables to service_role;

-- Backward-compatible RPC used by the production worker. The original
-- release_alert function also accepts price metadata; the worker contract
-- releases claims with the three arguments below. Delegate to the canonical
-- six-argument function and leave price metadata unchanged for this path.
create or replace function alert_platform.release_alert(
  p_alert_id uuid,
  p_worker_id uuid,
  p_next_check_at timestamptz
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
         claim_expires_at = null
   where id = p_alert_id
     and status = 'CLAIMED'
     and claimed_by = p_worker_id;

  get diagnostics v_updated = row_count;
  return v_updated > 0;
end;
$$;

grant execute on function alert_platform.release_alert(uuid, uuid, timestamptz) to service_role;
revoke all on function alert_platform.release_alert(uuid, uuid, timestamptz) from anon, authenticated;

update alert_platform.alerts
   set market = 'ITALIA'
 where upper(market) = 'ITALY';

update alert_platform.alerts
   set next_check_at = now()
 where status = 'ACTIVE' and next_check_at is null;

-- Recover only still-active, still-valid production rules. Historical TRIGGERED
-- rows remain in the archive and are never replayed as new notifications.
do $cutover$
begin
if to_regclass('public.trading_alerts_legacy_archive') is not null then
execute $sql$
insert into alert_platform.alerts (
  ticker, market, alert_type, condition, threshold, status, valid_until,
  next_check_at, created_at
)
select
  upper(trim(l.ticker)),
  case when upper(coalesce(l.market, 'USA')) in ('ITALY','ITALIA') then 'ITALIA' else 'USA' end,
  l.condition_type::alert_platform.alert_type,
  l.condition_type,
  l.trigger_level,
  'ACTIVE'::alert_platform.alert_status,
  l.expires_at,
  now(),
  l.created_at
from public.trading_alerts_legacy_archive l
where upper(l.status) = 'ACTIVE'
  and l.expires_at > now()
  and l.condition_type in ('PRICE_BELOW','PRICE_ABOVE','MAX_BUY','BREAKOUT','PULLBACK','SUPPORT','RESISTANCE')
  -- Exclude unmistakable smoke/test thresholds from production recovery.
  and not (upper(trim(l.ticker)) = 'MSFT' and l.condition_type = 'PRICE_ABOVE' and l.trigger_level = 1)
  and not exists (
    select 1 from alert_platform.alerts a
     where upper(a.ticker) = upper(trim(l.ticker))
       and upper(a.market) = case when upper(coalesce(l.market, 'USA')) in ('ITALY','ITALIA') then 'ITALIA' else 'USA' end
       and a.alert_type::text = l.condition_type
       and a.threshold is not distinct from l.trigger_level
       and a.status in ('ACTIVE','CLAIMED','TRIGGERED')
  )
$sql$;
end if;
end
$cutover$;

notify pgrst, 'reload schema';
commit;
