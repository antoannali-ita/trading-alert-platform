begin;

-- Seed one due session.
select alert_platform.ensure_market_session(
  'USA', current_date, now() - interval '10 minutes', now() - interval '7 minutes'
);

-- First worker wins the claim.
do $$
declare
  w1 uuid := '00000000-0000-0000-0000-000000000101';
  w2 uuid := '00000000-0000-0000-0000-000000000202';
  c1 integer;
  c2 integer;
begin
  select count(*) into c1
    from alert_platform.claim_session_refresh('USA', current_date, w1);
  if c1 <> 1 then raise exception 'first worker must claim exactly one session'; end if;

  select count(*) into c2
    from alert_platform.claim_session_refresh('USA', current_date, w2);
  if c2 <> 0 then raise exception 'second worker must not claim live lease'; end if;
end $$;

-- Ownership heartbeat succeeds only for owner.
do $$
declare
  sid uuid;
  ok boolean;
begin
  select id into sid from alert_platform.market_session_state
   where market='USA' and session_date=current_date;

  ok := alert_platform.heartbeat_session_refresh(
    sid, '00000000-0000-0000-0000-000000000202'
  );
  if ok then raise exception 'non-owner heartbeat must fail'; end if;

  ok := alert_platform.heartbeat_session_refresh(
    sid, '00000000-0000-0000-0000-000000000101'
  );
  if not ok then raise exception 'owner heartbeat must succeed'; end if;
end $$;

-- Expired lease can be recovered by another worker.
update alert_platform.market_session_state
   set claim_expires_at = now() - interval '1 second'
 where market='USA' and session_date=current_date;

do $$
declare c integer;
begin
  select count(*) into c
    from alert_platform.claim_session_refresh(
      'USA', current_date, '00000000-0000-0000-0000-000000000202'
    );
  if c <> 1 then raise exception 'expired lease must be reclaimable'; end if;
end $$;

-- Pending items force COMPLETED_WITH_PENDING.
do $$
declare sid uuid; ok boolean; st text;
begin
  select id into sid from alert_platform.market_session_state
   where market='USA' and session_date=current_date;

  insert into alert_platform.session_refresh_items(
    session_state_id,ticker,market,status,priority_class
  ) values (sid,'TSM','USA','PENDING_REFRESH','CRITICAL');

  ok := alert_platform.complete_session_refresh(
    sid, '00000000-0000-0000-0000-000000000202', '{"test":true}'::jsonb
  );
  if not ok then raise exception 'owner completion should succeed'; end if;

  select status into st from alert_platform.market_session_state where id=sid;
  if st <> 'COMPLETED_WITH_PENDING' then
    raise exception 'expected COMPLETED_WITH_PENDING, got %', st;
  end if;
end $$;

-- Completed session cannot be reclaimed.
do $$
declare c integer;
begin
  select count(*) into c from alert_platform.claim_session_refresh(
    'USA', current_date, '00000000-0000-0000-0000-000000000303'
  );
  if c <> 0 then raise exception 'completed session must not be reclaimable'; end if;
end $$;

-- Timeout path converts transient item back to PENDING_REFRESH and closes session.
select alert_platform.ensure_market_session(
  'ITALIA', current_date, now() - interval '30 minutes', now() - interval '27 minutes'
);

do $$
declare sid uuid; c integer; ok boolean; st text; item_st text;
begin
  select count(*) into c from alert_platform.claim_session_refresh(
    'ITALIA', current_date, '00000000-0000-0000-0000-000000000404'
  );
  if c <> 1 then raise exception 'italy session claim failed'; end if;

  select id into sid from alert_platform.market_session_state
   where market='ITALIA' and session_date=current_date;

  insert into alert_platform.session_refresh_items(
    session_state_id,ticker,market,status,priority_class
  ) values (sid,'ENI','ITALIA','PENDING_OPEN','HIGH');

  update alert_platform.market_session_state
     set refresh_started_at = now() - interval '16 minutes'
   where id=sid;

  ok := alert_platform.expire_session_refresh_if_needed(
    sid, '00000000-0000-0000-0000-000000000404'
  );
  if not ok then raise exception 'timed-out session should expire'; end if;

  select status into st from alert_platform.market_session_state where id=sid;
  if st <> 'COMPLETED_WITH_PENDING' then raise exception 'timeout session status wrong: %', st; end if;

  select status into item_st from alert_platform.session_refresh_items
   where session_state_id=sid and ticker='ENI';
  if item_st <> 'PENDING_REFRESH' then raise exception 'timeout item must be PENDING_REFRESH'; end if;
end $$;

-- alert_runs retry counter increments for consecutive provider errors.
do $$
declare
  aid uuid;
  r0 integer;
  r1 integer;
begin
  insert into alert_platform.alerts(ticker,market,alert_type,threshold,valid_until)
  values ('AAPL','USA','PRICE_BELOW',100,now()+interval '1 day') returning id into aid;

  select retry_count into r0 from alert_platform.record_alert_run(
    '00000000-0000-0000-0000-000000000505',aid,'AAPL',null,null,'TWELVE_DATA',null,'DATA_TIMEOUT',10
  );
  select retry_count into r1 from alert_platform.record_alert_run(
    '00000000-0000-0000-0000-000000000505',aid,'AAPL',null,null,'TWELVE_DATA',null,'DATA_TIMEOUT',10
  );
  if r0 <> 0 or r1 <> 1 then raise exception 'retry counts wrong: %, %', r0, r1; end if;
end $$;

rollback;
