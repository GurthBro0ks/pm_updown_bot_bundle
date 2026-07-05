# pm_weather_live_arm_single_cycle_host_policy_nonce_v2_20260705

## Summary

Weather cron was armed live under host-policy nonce approval with tiny
weather-only caps. One controlled weather live cycle ran and placed zero orders.
The operator then accepted the final live-armed cron state.

## Accepted State

- Manual QA: PASS_operator_accepted.
- Accepted weather live armed state: true.
- Accepted cron state: `WEATHER_DRY_RUN=false WEATHER_LIVE_ENABLED=true tiny_caps_present=yes`.
- Accepted proof: `/tmp/proof_pm_weather_live_arm_single_cycle_host_policy_nonce_v2_20260705T030545Z`.

## Safety

- No source commit or push in the arming phase.
- No service restart.
- No Caddy, DNS, systemd, timer, tmux, or Discord secret change.
- No main micro-live cron change.
- No main trading pause state change.
- No order placement or cancellation.
- No secrets printed.

## Rollback Target

If rollback is required, set the installed weather cron line back to:

- `WEATHER_DRY_RUN=true`
- `WEATHER_LIVE_ENABLED` absent or false
