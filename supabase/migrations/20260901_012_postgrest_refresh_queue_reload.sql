-- Recreate refresh queue claim RPC after final session_refresh_items shape
-- and explicitly reload PostgREST schema cache.

create or replace function alert_platform.claim_session_refresh_items(
  p_session_id uuid,
  p_worker_id uuid,
  p_limit integer
)
returns setof alert_platform.session_refresh_items
language plpgsql
security definer
set search_path = alert_platform, pg_temp
as $$
begin
  if not exists (
    select 1 from alert_platform.market_session_state
     where id=p_session_id and status='CLAIMED' and claimed_by=p_worker_id
  ) then return; end if;

  return query
  with candidates as (
    select i.id
      from alert_platform.session_refresh_items i
     where i.session_state_id=p_session_id
       and (
         i.status='PENDING_REFRESH'
         or (i.status='PENDING_OPEN' and i.next_retry_at <= now())
       )
     order by case i.priority_class when 'CRITICAL' then 1 when 'HIGH' then 2 when 'NORMAL' then 3 else 4 end,
              i.created_at,
              i.ticker
     limit greatest(1,p_limit)
     for update skip locked
  )
  update alert_platform.session_refresh_items i
     set status='PROCESSING', updated_at=now()
    from candidates c
   where i.id=c.id
  returning i.*;
end;
$$;

revoke execute on function alert_platform.claim_session_refresh_items(uuid,uuid,integer) from anon,authenticated;
grant execute on function alert_platform.claim_session_refresh_items(uuid,uuid,integer) to service_role;

notify pgrst, 'reload schema';
