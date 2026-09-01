-- Backend-only permissions for Trading Alert Platform.
-- Spec v1.2 FINAL: service role backend only; anon/authenticated do not receive direct schema access here.

revoke all on schema alert_platform from anon, authenticated;
grant usage on schema alert_platform to service_role;

grant execute on all functions in schema alert_platform to service_role;

-- Keep future RPCs backend-accessible without opening browser roles.
alter default privileges in schema alert_platform
  grant execute on functions to service_role;

-- Explicitly preserve browser-role denial at schema level.
revoke all on all tables in schema alert_platform from anon, authenticated;
revoke all on all sequences in schema alert_platform from anon, authenticated;
revoke execute on all functions in schema alert_platform from anon, authenticated;
