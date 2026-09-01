-- Helpers for scheduler coordination between session refresh and adaptive checks.

create or replace function alert_platform.session_refresh_pending_count(
  p_session_id uuid,
  p_worker_id uuid
)
returns integer
language sql
stable
security definer
set search_path = alert_platform, pg_temp
as $$
  select case
    when not exists (
      select 1 from alert_platform.market_session_state
       where id=p_session_id and status='CLAIMED' and claimed_by=p_worker_id
    ) then -1
    else (
      select count(*)::integer
        from alert_platform.session_refresh_items
       where session_state_id=p_session_id
         and status in ('PENDING_REFRESH','PENDING_OPEN','PROCESSING')
    )
  end
$$;

create or replace function alert_platform.claim_due_alerts_excluding_markets(
  p_worker_id uuid,
  p_limit integer,
  p_excluded_markets text[] default '{}'::text[]
)
returns setof alert_platform.alerts
language plpgsql
security definer
set search_path = alert_platform, pg_temp
as $$
declare
  v_lease_seconds integer := alert_platform.get_config_int('claim_lease_seconds', 180);
begin
  if p_worker_id is null then raise exception 'p_worker_id is required'; end if;
  if p_limit is null or p_limit <= 0 then raise exception 'p_limit must be > 0'; end if;

  return query
  with candidates as (
    select a.id
      from alert_platform.alerts a
     where (
       (a.status='ACTIVE' and a.next_check_at <= now())
       or (a.status='CLAIMED' and a.claim_expires_at < now())
     )
       and a.valid_until >= now()
       and not (upper(a.market) = any(
         coalesce((select array_agg(upper(x)) from unnest(p_excluded_markets) x), '{}'::text[])
       ))
     order by
       case when a.status='CLAIMED' then 0 else 1 end,
       a.priority desc,
       a.next_check_at asc nulls first,
       a.created_at asc
     for update skip locked
     limit p_limit
  )
  update alert_platform.alerts a
     set status='CLAIMED',
         claimed_at=now(),
         claimed_by=p_worker_id,
         claim_expires_at=now()+make_interval(secs=>v_lease_seconds)
    from candidates c
   where a.id=c.id
  returning a.*;
end;
$$;

revoke execute on function alert_platform.session_refresh_pending_count(uuid,uuid) from anon,authenticated;
revoke execute on function alert_platform.claim_due_alerts_excluding_markets(uuid,integer,text[]) from anon,authenticated;
grant execute on function alert_platform.session_refresh_pending_count(uuid,uuid) to service_role;
grant execute on function alert_platform.claim_due_alerts_excluding_markets(uuid,integer,text[]) to service_role;
