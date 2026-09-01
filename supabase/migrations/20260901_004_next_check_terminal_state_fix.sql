-- Allow terminal/non-schedulable alerts to clear next_check_at.
-- Spec v1.2 state machine uses NULL next_check_at for terminal states.

alter table alert_platform.alerts
  alter column next_check_at drop not null;

comment on column alert_platform.alerts.next_check_at is
  'Next scheduled market-data check. NULL when alert is terminal or no longer schedulable.';
