-- Add entry/max-buy context to each deduplicated refresh ticker.

alter table alert_platform.session_refresh_items
  add column if not exists entry_min numeric,
  add column if not exists entry_max numeric,
  add column if not exists max_buy numeric;

create or replace function alert_platform.seed_session_refresh_items(
  p_session_id uuid,
  p_worker_id uuid
)
returns integer
language plpgsql
security definer
set search_path = alert_platform, pg_temp
as $$
declare
  v_market text;
  v_count integer;
begin
  select market into v_market
    from alert_platform.market_session_state
   where id=p_session_id and status='CLAIMED' and claimed_by=p_worker_id;
  if v_market is null then return 0; end if;

  with grouped as (
    select a.ticker,
           min(a.threshold_min) filter (where a.alert_type='ENTRY_ZONE') as entry_min,
           max(a.threshold_max) filter (where a.alert_type='ENTRY_ZONE') as entry_max,
           max(a.threshold) filter (where a.alert_type='MAX_BUY') as max_buy,
           min(
             case
               when a.last_price is null or a.last_price <= 0 then 3
               when a.alert_type='ENTRY_ZONE' and a.threshold_min is not null and a.threshold_max is not null then
                 case
                   when a.last_price between a.threshold_min and a.threshold_max then 1
                   when least(abs(a.last_price-a.threshold_min),abs(a.last_price-a.threshold_max))/a.last_price < 0.02 then 1
                   when least(abs(a.last_price-a.threshold_min),abs(a.last_price-a.threshold_max))/a.last_price < 0.05 then 2
                   else 3
                 end
               when a.threshold is not null then
                 case
                   when abs(a.last_price-a.threshold)/a.last_price < 0.02 then 1
                   when abs(a.last_price-a.threshold)/a.last_price < 0.05 then 2
                   else 3
                 end
               else 3
             end
           ) as rank
      from alert_platform.alerts a
     where a.status='ACTIVE'
       and upper(a.market)=upper(v_market)
       and a.valid_until > now()
     group by a.ticker
  )
  insert into alert_platform.session_refresh_items(
    session_state_id,ticker,market,priority_class,status,entry_min,entry_max,max_buy
  )
  select p_session_id, upper(ticker), upper(v_market),
         case rank when 1 then 'CRITICAL' when 2 then 'HIGH' else 'NORMAL' end,
         'PENDING_REFRESH',entry_min,entry_max,max_buy
    from grouped
  on conflict(session_state_id,ticker) do update
    set entry_min=excluded.entry_min,
        entry_max=excluded.entry_max,
        max_buy=excluded.max_buy,
        updated_at=now();
  get diagnostics v_count = row_count;
  return v_count;
end;
$$;
