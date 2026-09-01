begin;

-- First reservation is capped by per-minute limit.
do $$
declare a integer; b integer; used integer;
begin
  a := alert_platform.reserve_provider_credits('TWELVE_DATA', 10, 8, 800);
  if a <> 8 then raise exception 'expected first reservation 8, got %', a; end if;

  b := alert_platform.reserve_provider_credits('TWELVE_DATA', 3, 8, 800);
  if b <> 0 then raise exception 'same-minute reservation must be exhausted, got %', b; end if;

  used := alert_platform.provider_credits_used_today('TWELVE_DATA');
  if used <> 8 then raise exception 'daily usage expected 8, got %', used; end if;
end $$;

-- Separate provider has independent budget.
do $$
declare a integer;
begin
  a := alert_platform.reserve_provider_credits('OTHER', 4, 5, 5);
  if a <> 4 then raise exception 'other provider expected 4, got %', a; end if;
end $$;

rollback;
