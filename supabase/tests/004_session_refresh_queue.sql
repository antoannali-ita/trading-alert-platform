begin;

insert into alert_platform.alerts(ticker,market,alert_type,threshold,last_price,valid_until)
values
 ('NEAR','USA','PRICE_BELOW',100,101,now()+interval '1 day'),
 ('MID','USA','PRICE_BELOW',100,103,now()+interval '1 day'),
 ('FAR','USA','PRICE_BELOW',100,120,now()+interval '1 day');

select alert_platform.ensure_market_session(
 'USA',current_date,now()-interval '10 min',now()-interval '7 min'
);

do $$
declare sid uuid; c integer; first_ticker text; ok boolean; updated integer;
begin
  select count(*) into c from alert_platform.claim_session_refresh(
    'USA',current_date,'00000000-0000-0000-0000-000000000601'
  );
  if c<>1 then raise exception 'session claim failed'; end if;

  select id into sid from alert_platform.market_session_state
   where market='USA' and session_date=current_date;

  c := alert_platform.seed_session_refresh_items(
    sid,'00000000-0000-0000-0000-000000000601'
  );
  if c<>3 then raise exception 'expected 3 seeded items, got %',c; end if;

  select ticker into first_ticker
    from alert_platform.claim_session_refresh_items(
      sid,'00000000-0000-0000-0000-000000000601',1
    );
  if first_ticker<>'NEAR' then raise exception 'CRITICAL ticker must be first, got %',first_ticker; end if;

  ok := alert_platform.update_session_refresh_item(
    sid,'00000000-0000-0000-0000-000000000601','NEAR','UPDATED',
    99.5,now(),99,101,100,98,100000,'TWELVE_DATA','PRIMARY_OK',-0.0198,
    array['GAP_MATERIAL','GAP_THROUGH_TRIGGER'],null,null
  );
  if not ok then raise exception 'item update failed'; end if;

  updated := alert_platform.apply_session_price_to_alerts(
    sid,'00000000-0000-0000-0000-000000000601','NEAR',99.5,now(),'TWELVE_DATA',now()+interval '5 min'
  );
  if updated<>1 then raise exception 'expected one alert update, got %',updated; end if;
end $$;

rollback;
