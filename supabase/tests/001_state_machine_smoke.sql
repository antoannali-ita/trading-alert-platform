-- Trading Alert Platform smoke tests
-- Run after migrations in an isolated test database.
-- Each block raises on failure. Transaction is rolled back at the end.

begin;

set search_path = alert_platform, public, pg_temp;

-- Helpers
create or replace function pg_temp.assert_true(p_value boolean, p_message text)
returns void language plpgsql as $$
begin
  if coalesce(p_value, false) is not true then
    raise exception 'ASSERT FAILED: %', p_message;
  end if;
end;
$$;

-- ------------------------------------------------------------
-- 1. Config validation: invalid cast must fail explicitly.
-- ------------------------------------------------------------
do $$
begin
  begin
    perform alert_platform.set_system_config('claim_lease_seconds', 'banana');
    raise exception 'ASSERT FAILED: invalid integer config was accepted';
  exception
    when others then
      if position('Invalid integer config' in sqlerrm) = 0 then
        raise;
      end if;
  end;
end;
$$;

-- Restore valid config after failed statement/exception scope.
select alert_platform.set_system_config('claim_lease_seconds', '180');

-- Cross-key validation.
do $$
begin
  begin
    perform alert_platform.set_system_config('heartbeat_threshold_seconds', '180');
    raise exception 'ASSERT FAILED: heartbeat >= lease was accepted';
  exception
    when others then
      if position('heartbeat_threshold_seconds must be < claim_lease_seconds' in sqlerrm) = 0 then
        raise;
      end if;
  end;
end;
$$;
select alert_platform.set_system_config('heartbeat_threshold_seconds', '60');

-- ------------------------------------------------------------
-- 2. Insert MVP alerts.
-- ------------------------------------------------------------
insert into alert_platform.alerts(
  id, ticker, market, alert_type, condition, threshold,
  valid_until, next_check_at, priority
) values
  ('00000000-0000-0000-0000-000000000101','AAA','USA','PRICE_BELOW','<=',100,now()+interval '1 day',now()-interval '1 minute',90),
  ('00000000-0000-0000-0000-000000000102','BBB','USA','PRICE_ABOVE','>=',200,now()+interval '1 day',now()-interval '1 minute',80),
  ('00000000-0000-0000-0000-000000000103','CCC','USA','PRICE_BELOW','<=',50,now()+interval '1 day',now()-interval '1 minute',70),
  ('00000000-0000-0000-0000-000000000104','DDD','USA','PRICE_BELOW','<=',75,now()+interval '1 day',now()-interval '1 minute',60);

-- ------------------------------------------------------------
-- 3. Normal atomic claim.
-- ------------------------------------------------------------
do $$
declare
  v_worker uuid := '10000000-0000-0000-0000-000000000001';
  v_count int;
begin
  select count(*) into v_count
  from alert_platform.claim_due_alerts(v_worker, 2);

  perform pg_temp.assert_true(v_count = 2, 'claim_due_alerts should claim 2 rows');
  perform pg_temp.assert_true(
    (select count(*) = 2 from alert_platform.alerts where claimed_by = v_worker and status='CLAIMED'),
    'claimed rows should be owned by worker'
  );
end;
$$;

-- ------------------------------------------------------------
-- 4. Expired claim recovery.
-- Force AAA lease expired, then reclaim with another worker.
-- ------------------------------------------------------------
update alert_platform.alerts
set status='CLAIMED',
    claimed_by='10000000-0000-0000-0000-000000000001',
    claimed_at=now()-interval '10 minutes',
    claim_expires_at=now()-interval '1 minute'
where id='00000000-0000-0000-0000-000000000101';

do $$
declare
  v_worker uuid := '10000000-0000-0000-0000-000000000002';
  v_seen boolean;
begin
  select exists(
    select 1
    from alert_platform.claim_due_alerts(v_worker, 10) a
    where a.id='00000000-0000-0000-0000-000000000101'
  ) into v_seen;

  perform pg_temp.assert_true(v_seen, 'expired CLAIMED alert must be reclaimable');
  perform pg_temp.assert_true(
    (select claimed_by=v_worker and status='CLAIMED'
       from alert_platform.alerts
      where id='00000000-0000-0000-0000-000000000101'),
    'reclaimed alert must have new owner'
  );
end;
$$;

-- ------------------------------------------------------------
-- 5. Ownership loss: old worker cannot release reclaimed alert.
-- ------------------------------------------------------------
do $$
declare
  v_ok boolean;
begin
  select alert_platform.release_alert(
    '00000000-0000-0000-0000-000000000101',
    '10000000-0000-0000-0000-000000000001',
    now()+interval '5 minutes', 99, now(), 'TEST'
  ) into v_ok;

  perform pg_temp.assert_true(v_ok=false, 'old owner must not release reclaimed alert');
end;
$$;

-- ------------------------------------------------------------
-- 6. Cancel anti-race.
-- Live claim cannot be cancelled.
-- ------------------------------------------------------------
do $$
declare
  v_ok boolean;
begin
  select alert_platform.cancel_alert('00000000-0000-0000-0000-000000000101') into v_ok;
  perform pg_temp.assert_true(v_ok=false, 'live claimed alert must not be cancelled');
end;
$$;

-- Expired claim can be cancelled.
update alert_platform.alerts
set claim_expires_at=now()-interval '1 second'
where id='00000000-0000-0000-0000-000000000101';

do $$
declare
  v_ok boolean;
begin
  select alert_platform.cancel_alert('00000000-0000-0000-0000-000000000101') into v_ok;
  perform pg_temp.assert_true(v_ok=true, 'expired claimed alert should be cancellable');
  perform pg_temp.assert_true(
    (select status='CANCELLED' from alert_platform.alerts where id='00000000-0000-0000-0000-000000000101'),
    'cancelled alert status expected'
  );
end;
$$;

-- ------------------------------------------------------------
-- 7. Multi-alert consolidated trigger: 2 alerts, 1 event.
-- Prepare CCC + DDD under same live owner.
-- ------------------------------------------------------------
update alert_platform.alerts
set status='CLAIMED',
    claimed_by='20000000-0000-0000-0000-000000000001',
    claimed_at=now(),
    claim_expires_at=now()+interval '3 minutes'
where id in (
  '00000000-0000-0000-0000-000000000103',
  '00000000-0000-0000-0000-000000000104'
);

do $$
declare
  v_event uuid;
  v_ids uuid[] := array[
    '00000000-0000-0000-0000-000000000103'::uuid,
    '00000000-0000-0000-0000-000000000104'::uuid
  ];
begin
  select alert_platform.create_trigger_event(
    '20000000-0000-0000-0000-000000000001',
    v_ids,
    'GROUPTEST', 'USA', 49.5, now(), 'TEST', 'BUY_PREBUY_HIGH'
  ) into v_event;

  perform pg_temp.assert_true(v_event is not null, 'trigger event id expected');
  perform pg_temp.assert_true(
    (select count(*)=2 from alert_platform.trigger_event_alerts where trigger_event_id=v_event),
    'trigger event must link both alerts'
  );
  perform pg_temp.assert_true(
    (select count(*)=2 from alert_platform.alerts where trigger_event_id=v_event and status='TRIGGERED'),
    'both alerts must be TRIGGERED'
  );
  perform pg_temp.assert_true(
    (select count(*)=1 from alert_platform.trigger_events where trigger_event_id=v_event),
    'exactly one consolidated trigger event expected'
  );

  -- Notification idempotency: duplicate same channel/event must violate unique key.
  insert into alert_platform.notifications(trigger_event_id,ticker,notification_type)
  values(v_event,'GROUPTEST','WHATSAPP');

  begin
    insert into alert_platform.notifications(trigger_event_id,ticker,notification_type)
    values(v_event,'GROUPTEST','WHATSAPP');
    raise exception 'ASSERT FAILED: duplicate notification was accepted';
  exception
    when unique_violation then null;
  end;
end;
$$;

-- ------------------------------------------------------------
-- 8. Trigger ownership must reject a lost/expired claim.
-- ------------------------------------------------------------
insert into alert_platform.alerts(
  id,ticker,market,alert_type,condition,threshold,valid_until,next_check_at,
  status,claimed_by,claimed_at,claim_expires_at
) values (
  '00000000-0000-0000-0000-000000000105','EEE','USA','PRICE_BELOW','<=',10,
  now()+interval '1 day',now(),'CLAIMED',
  '30000000-0000-0000-0000-000000000001',now()-interval '5 minutes',now()-interval '1 minute'
);

do $$
begin
  begin
    perform alert_platform.create_trigger_event(
      '30000000-0000-0000-0000-000000000001',
      array['00000000-0000-0000-0000-000000000105'::uuid],
      'EEE','USA',9.5,now(),'TEST','BUY_ONLY'
    );
    raise exception 'ASSERT FAILED: expired claim triggered an event';
  exception
    when others then
      if position('LOCK_ERROR' in sqlerrm)=0 then
        raise;
      end if;
  end;
end;
$$;

rollback;
