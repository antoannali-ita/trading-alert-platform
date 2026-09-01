-- Session refresh queue RPCs. All mutations require ownership of the claimed session.

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

  with ranked as (
    select a.ticker,
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
    session_state_id,ticker,market,priority_class,status
  )
  select p_session_id, upper(ticker), upper(v_market),
         case rank when 1 then 'CRITICAL' when 2 then 'HIGH' else 'NORMAL' end,
         'PENDING_REFRESH'
    from ranked
  on conflict(session_state_id,ticker) do nothing;
  get diagnostics v_count = row_count;
  return v_count;
end;
$$;

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

create or replace function alert_platform.update_session_refresh_item(
  p_session_id uuid,
  p_worker_id uuid,
  p_ticker text,
  p_status text,
  p_current_price numeric default null,
  p_price_timestamp timestamptz default null,
  p_open_price numeric default null,
  p_previous_close numeric default null,
  p_high_price numeric default null,
  p_low_price numeric default null,
  p_volume numeric default null,
  p_provider text default null,
  p_data_quality text default null,
  p_gap_pct numeric default null,
  p_gap_flags text[] default null,
  p_error_code text default null,
  p_next_retry_at timestamptz default null
)
returns boolean
language plpgsql
security definer
set search_path = alert_platform, pg_temp
as $$
declare
  v_count integer;
begin
  if p_status not in ('PENDING_REFRESH','PENDING_OPEN','PROCESSING','UPDATED','DEGRADED','NO_OPEN_DATA','FAILED') then
    raise exception 'invalid refresh item status: %', p_status;
  end if;

  if not exists (
    select 1 from alert_platform.market_session_state
     where id=p_session_id and status='CLAIMED' and claimed_by=p_worker_id
  ) then return false; end if;

  update alert_platform.session_refresh_items
     set status=p_status,
         current_price=coalesce(p_current_price,current_price),
         price_timestamp=coalesce(p_price_timestamp,price_timestamp),
         open_price=coalesce(p_open_price,open_price),
         previous_close=coalesce(p_previous_close,previous_close),
         high_price=coalesce(p_high_price,high_price),
         low_price=coalesce(p_low_price,low_price),
         volume=coalesce(p_volume,volume),
         provider=coalesce(p_provider,provider),
         data_quality=coalesce(p_data_quality,data_quality),
         gap_pct=p_gap_pct,
         gap_flags=p_gap_flags,
         error_code=p_error_code,
         next_retry_at=p_next_retry_at,
         retry_count=case when p_status='PENDING_OPEN' then retry_count+1 else retry_count end,
         processed_at=case when p_status in ('UPDATED','DEGRADED','NO_OPEN_DATA','FAILED') then now() else processed_at end,
         updated_at=now()
   where session_state_id=p_session_id and ticker=upper(p_ticker);
  get diagnostics v_count=row_count;
  return v_count=1;
end;
$$;

create or replace function alert_platform.apply_session_price_to_alerts(
  p_session_id uuid,
  p_worker_id uuid,
  p_ticker text,
  p_price numeric,
  p_price_timestamp timestamptz,
  p_provider text,
  p_next_check_at timestamptz
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

  update alert_platform.alerts
     set last_price=p_price,
         last_price_at=p_price_timestamp,
         last_price_provider=p_provider,
         next_check_at=p_next_check_at,
         updated_at=now()
   where ticker=upper(p_ticker)
     and upper(market)=upper(v_market)
     and status='ACTIVE'
     and valid_until>now();
  get diagnostics v_count=row_count;
  return v_count;
end;
$$;

revoke execute on function alert_platform.seed_session_refresh_items(uuid,uuid) from anon,authenticated;
revoke execute on function alert_platform.claim_session_refresh_items(uuid,uuid,integer) from anon,authenticated;
revoke execute on function alert_platform.update_session_refresh_item(uuid,uuid,text,text,numeric,timestamptz,numeric,numeric,numeric,numeric,numeric,text,text,numeric,text[],text,timestamptz) from anon,authenticated;
revoke execute on function alert_platform.apply_session_price_to_alerts(uuid,uuid,text,numeric,timestamptz,text,timestamptz) from anon,authenticated;

grant execute on function alert_platform.seed_session_refresh_items(uuid,uuid) to service_role;
grant execute on function alert_platform.claim_session_refresh_items(uuid,uuid,integer) to service_role;
grant execute on function alert_platform.update_session_refresh_item(uuid,uuid,text,text,numeric,timestamptz,numeric,numeric,numeric,numeric,numeric,text,text,numeric,text[],text,timestamptz) to service_role;
grant execute on function alert_platform.apply_session_price_to_alerts(uuid,uuid,text,numeric,timestamptz,text,timestamptz) to service_role;
