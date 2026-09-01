-- Atomic provider credit budget shared by concurrent workers.

create table if not exists alert_platform.provider_credit_usage (
  provider text not null,
  minute_bucket timestamptz not null,
  credits_used integer not null default 0 check (credits_used >= 0),
  updated_at timestamptz not null default now(),
  primary key (provider, minute_bucket)
);

create index if not exists idx_provider_credit_usage_day
  on alert_platform.provider_credit_usage(provider, minute_bucket desc);

create or replace function alert_platform.reserve_provider_credits(
  p_provider text,
  p_requested integer,
  p_per_minute_limit integer,
  p_daily_budget integer
)
returns integer
language plpgsql
security definer
set search_path = alert_platform, pg_temp
as $$
declare
  v_provider text := upper(p_provider);
  v_bucket timestamptz := date_trunc('minute', now());
  v_minute_used integer := 0;
  v_day_used integer := 0;
  v_allowed integer := 0;
begin
  if p_requested <= 0 or p_per_minute_limit <= 0 or p_daily_budget <= 0 then
    return 0;
  end if;

  perform pg_advisory_xact_lock(hashtext(v_provider));

  select coalesce(credits_used,0) into v_minute_used
    from alert_platform.provider_credit_usage
   where provider=v_provider and minute_bucket=v_bucket;

  select coalesce(sum(credits_used),0) into v_day_used
    from alert_platform.provider_credit_usage
   where provider=v_provider
     and minute_bucket >= date_trunc('day', now())
     and minute_bucket < date_trunc('day', now()) + interval '1 day';

  v_allowed := least(
    p_requested,
    greatest(0, p_per_minute_limit - v_minute_used),
    greatest(0, p_daily_budget - v_day_used)
  );

  if v_allowed > 0 then
    insert into alert_platform.provider_credit_usage(provider,minute_bucket,credits_used)
    values(v_provider,v_bucket,v_allowed)
    on conflict(provider,minute_bucket) do update
      set credits_used = alert_platform.provider_credit_usage.credits_used + excluded.credits_used,
          updated_at = now();
  end if;

  return v_allowed;
end;
$$;

create or replace function alert_platform.provider_credits_used_today(p_provider text)
returns integer
language sql
stable
security definer
set search_path = alert_platform, pg_temp
as $$
  select coalesce(sum(credits_used),0)::integer
    from alert_platform.provider_credit_usage
   where provider=upper(p_provider)
     and minute_bucket >= date_trunc('day', now())
     and minute_bucket < date_trunc('day', now()) + interval '1 day'
$$;

alter table alert_platform.provider_credit_usage enable row level security;
revoke all on alert_platform.provider_credit_usage from anon, authenticated;
revoke execute on function alert_platform.reserve_provider_credits(text,integer,integer,integer) from anon, authenticated;
revoke execute on function alert_platform.provider_credits_used_today(text) from anon, authenticated;
grant execute on function alert_platform.reserve_provider_credits(text,integer,integer,integer) to service_role;
grant execute on function alert_platform.provider_credits_used_today(text) to service_role;
