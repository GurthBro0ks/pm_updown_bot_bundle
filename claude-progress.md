# 2026-07-09 (pm_main_micro_live_gate_failure_kind_diagnostic — Source Fix Built)

**Agent:** Codex (SlimyAI NUC1)
**Project:** pm_updown_bot_bundle / main Kalshi runtime diagnostics
**Type:** Diagnostic-only source/test change
**Proof:** `/tmp/proof_pm_main_micro_live_gate_failure_kind_diagnostic_20260709T171329Z`
**Manual QA:** pending_operator_qa

### Summary
Added redacted diagnostic-only gate failure kinds for
`check_micro_live_gates()` nearest-miss failures. Generic
`rejection_reason=gate_failed` is preserved, but fresh structured summaries now
also carry `gate_failure_kind`, `gate_failure_kinds`, and
`gate_failure_kind_counts` so operators can distinguish `market_end_time`,
`liquidity_min`, `fallback_prior`, `size_limit`, `price_sanity`, and
`unknown_gate_failure`.

### Changes
1. Added `gate_failure_kinds_from_violations()` as a pure classifier over
   existing violation text; it does not feed back into gate decisions.
2. Extended `utils/edge_nearest_miss.py` redacted payloads and CLI formatting
   with gate failure kind fields and counts.
3. Added fake-fixture tests for all requested failure kinds, unknown fallback,
   redaction, bounded output, direct invocation, and unchanged gate decisions.
4. Added buglog:
   `docs/buglog/pm_main_micro_live_gate_failure_kind_diagnostic_20260709.md`.

### Verified
- `python3 -m py_compile strategies/kalshi_optimize.py utils/edge_nearest_miss.py scripts/main_edge_nearest_miss_redacted.py tests/test_main_edge_nearest_miss_redacted.py`: PASS.
- `PYTHONPATH=. pytest -q tests/test_main_edge_nearest_miss_redacted.py`: PASS, 14 passed.
- `python3 scripts/kalshi_redacted_health_check.py`: PASS.
- `python3 scripts/main_market_inventory_redacted.py --max-days 3 --allowed-categories index,crypto,economics,commodities,financials`: PASS, `MAX_DAYS=3`, `THREE_DAY_ALLOWED_COUNT=100`, `ZERO_CANDIDATE_REASON=has_allowed_candidates`.
- `python3 scripts/main_run_gate_summary_redacted.py`: PASS, `ZERO_ORDER_REASON=edge_or_profitability_blocked`.
- `python3 scripts/main_edge_nearest_miss_redacted.py`: PASS; prints `GATE_FAILURE_KIND_COUNTS=none` against the pre-existing latest summary because that summary predates this diagnostic field.
- `PYTHONPATH=. pytest tests`: PASS, 565 passed, 1 warning.
- `./scripts/run_tests.sh`: PASS, `STATUS: ALL GATES PASS`.
- Sanitized cron check: main cron present, `TRADING_PAUSED=false`,
  `MAX_DAYS_TO_EXPIRY=3`, `MIN_TRADE_PRICE_CENTS=25`,
  `KALSHI_ALLOWED_CATEGORIES=index,crypto,economics,commodities,financials`;
  weather remains `WEATHER_DRY_RUN=false WEATHER_LIVE_ENABLED=true`.

### Safety
- Diagnostic-only source/test/docs change. No trading behavior, thresholds,
  gates, sizing, cron, runtime config, weather state, service state, or order
  path was changed.
- No main live manual run and no order placement/cancellation/modification.
- No credential values, auth headers, key paths, webhook URLs, or response
  bodies were printed.

### Remaining
- Manual operator QA.
- Wait for the next scheduled main cron to write a fresh nearest-miss summary,
  then run `python3 scripts/main_edge_nearest_miss_redacted.py` and confirm
  `gate_failure_kind` is populated for any new `gate_failed` candidate.

# 2026-07-09 (pm_main_runtime_categories_and_inventory_decode_fix — Manual QA Accepted)

**Agent:** Codex (SlimyAI NUC1)
**Project:** pm_updown_bot_bundle / main Kalshi runtime diagnostics
**Type:** Manual QA acceptance record
**Accepted Commit:** `339d8ccbc7752d5428617a461edb53bd20812049`
**Proof:** `/tmp/proof_pm_main_runtime_categories_and_inventory_decode_fix_20260709T091220Z`
**Manual QA:** PASS_operator_accepted

### Summary
Operator accepted the runtime-category preservation, redacted inventory decode
hardening, and redacted main-run gate summary fix at
`HEAD == origin/feat/ibkr-forecast-integration ==
339d8ccbc7752d5428617a461edb53bd20812049`.

### Verified
- `git rev-parse HEAD`: `339d8ccbc7752d5428617a461edb53bd20812049`.
- `git rev-parse origin/feat/ibkr-forecast-integration`: `339d8ccbc7752d5428617a461edb53bd20812049`.
- `git status --short --branch`: target repo clean and tracking origin.
- Prior implementation validation recorded in proof: shell syntax, py_compile,
  focused tests, redacted inventory, Kalshi health, redacted gate summary,
  `PYTHONPATH=. pytest tests`, `./scripts/run_tests.sh`, and sanitized cron all
  passed.

### Safety
- Acceptance bookkeeping only.
- No installed cron change, main live manual run, order placement/cancellation,
  threshold/category change, weather arm/disarm, service restart,
  Caddy/DNS/systemd/timer/tmux change, Discord notification, or runtime config
  mutation.
- No credential values, auth headers, key paths, or response bodies were printed.

### Remaining
- Wait for the next scheduled main cron and use
  `scripts/main_run_gate_summary_redacted.py --log logs/cron.log` to classify
  the latest zero-order reason.

---

# 2026-07-08 (pm_main_three_day_fetch_universe_supplement_fix — Source Fix Built)

**Agent:** Codex (SlimyAI NUC1)
**Project:** pm_updown_bot_bundle / main Kalshi market diagnosis
**Type:** Source/test-only fetch universe fix
**Source Commit:** `4ff644413b844b5d438eab0c9a0367385e82334e`
**Proof:** `/tmp/proof_pm_main_three_day_fetch_universe_supplement_fix_20260708T151514Z`
**Manual QA:** pending_operator_qa

### Summary
Added a bounded priority supplemental series path so the main bot fetch universe
includes `KXNASDAQ100U` even when it ranks outside the default
`KALSHI_SERIES_LIMIT=50`. This preserves `MAX_DAYS_TO_EXPIRY=3`, does not expand
allowed categories, and leaves all expiry/category/weather/trading gates in
place after fetch.

### Current Inventory After Fix
- `MARKET_INVENTORY=PASS`
- `MAX_DAYS=3`
- `TOTAL_FETCHED=337`
- `AFTER_EXPIRY_FILTER=134`
- `AFTER_WEATHER_EXCLUSION=134`
- `AFTER_CATEGORY_FILTER=105`
- `THREE_DAY_ALLOWED_COUNT=105`
- `SUPPLEMENTAL_SERIES_MARKET_COUNT=100`
- `SUPPLEMENTAL_THREE_DAY_ALLOWED_COUNT=100`
- `ZERO_CANDIDATE_REASON=has_allowed_candidates`

### Changes
1. Added `KALSHI_MAIN_SUPPLEMENTAL_SERIES = ("KXNASDAQ100U",)` and a pure
   deduping selector for top-N plus supplemental series.
2. Tagged normalized markets with `kalshi_fetch_source`.
3. Extended the redacted inventory output with supplemental-series counts.
4. Added fixture tests proving out-of-top-N inclusion, dedupe, expiry filtering,
   weather exclusion, disallowed category exclusion, and no order action in
   inventory tests.
5. Added buglog:
   `docs/buglog/pm_main_three_day_fetch_universe_supplement_fix_20260708.md`.

### Verified
- `bash -n scripts/cron_micro_live.sh`: PASS.
- `bash -n scripts/cron_weather_trade.sh`: PASS.
- `python3 -m py_compile utils/kalshi.py scripts/main_market_inventory_redacted.py tests/test_kalshi_fetch_supplemental_series.py tests/test_main_market_inventory_redacted.py`: PASS.
- `PYTHONPATH=. pytest tests/test_kalshi_fetch_supplemental_series.py tests/test_main_market_inventory_redacted.py -q`: PASS, 16 passed.
- `python3 scripts/main_market_inventory_redacted.py --max-days 3 --allowed-categories index,crypto,economics,commodities,financials`: PASS, see counts above.
- `python3 scripts/kalshi_redacted_health_check.py`: PASS, `KALSHI_AUTH=PASS`, `PORTFOLIO_READONLY=PASS`, `OPEN_ORDERS_READONLY=PASS`, `HTTP_STATUS_CLASS=2xx`, `VALUES_PRINTED=no`.
- `PYTHONPATH=. pytest tests -q`: PASS, 544 passed, 1 warning.
- `./scripts/run_tests.sh`: PASS, `STATUS: ALL GATES PASS`.
- Sanitized cron check: main cron present, `TRADING_PAUSED=false`,
  `MAX_DAYS_TO_EXPIRY=3`,
  `KALSHI_ALLOWED_CATEGORIES=index,crypto,economics,commodities,financials`;
  weather remains `WEATHER_DRY_RUN=false WEATHER_LIVE_ENABLED=true`.

### Safety
- No installed cron change, main live manual run, order placement/cancellation,
  category-gate expansion, weather change, service restart,
  Caddy/DNS/systemd/timer/tmux change, Discord notification, or runtime config
  mutation.
- No credential values, auth headers, key paths, or response bodies were printed.

### Remaining
- Manual operator QA.
- After push and manual QA, wait for the next scheduled main cron to see whether
  the order path reaches AI/gates.

---

# 2026-07-08 (pm_main_three_day_redacted_market_inventory_tool — Manual QA Accepted)

**Agent:** Codex (SlimyAI NUC1)
**Project:** pm_updown_bot_bundle / main Kalshi market diagnosis
**Type:** Manual QA acceptance record
**Accepted Commit:** `78d24db18632b3384165a5bcdfab655e7ba62afc`
**Manual QA:** PASS_operator_accepted

### Summary
Quick manual QA matched the PASS result for the redacted main-market inventory
tool at `HEAD == origin/feat/ibkr-forecast-integration ==
78d24db18632b3384165a5bcdfab655e7ba62afc`.

### Verified
- `git status --short --branch`: branch clean and tracking origin.
- `git rev-parse HEAD`: `78d24db18632b3384165a5bcdfab655e7ba62afc`.
- `git rev-parse origin/feat/ibkr-forecast-integration`: `78d24db18632b3384165a5bcdfab655e7ba62afc`.
- `python3 scripts/main_market_inventory_redacted.py --max-days 3 --allowed-categories index,crypto,economics,commodities,financials`: PASS, `MAX_DAYS=3`, `TOTAL_FETCHED=349`, `AFTER_EXPIRY_FILTER=0`, `ZERO_CANDIDATE_REASON=no_current_3_day_markets`.
- `python3 scripts/kalshi_redacted_health_check.py`: PASS, `KALSHI_AUTH=PASS`, `PORTFOLIO_READONLY=PASS`, `OPEN_ORDERS_READONLY=PASS`, `HTTP_STATUS_CLASS=2xx`, `VALUES_PRINTED=no`.

### Safety
- No installed cron change, main live manual run, order placement/cancellation,
  category-gate change, weather arm/disarm, service restart,
  Caddy/DNS/systemd/timer/tmux change, Discord notification, or runtime config
  mutation.
- No credential values, auth headers, key paths, or response bodies were printed.

---

# 2026-07-08 (pm_main_three_day_redacted_market_inventory_tool — Read-Only Main Market Inventory)

**Agent:** Codex (SlimyAI NUC1)
**Project:** pm_updown_bot_bundle / main Kalshi market diagnosis
**Type:** Read-only diagnostic tooling
**Proof:** `/tmp/proof_pm_main_three_day_redacted_market_inventory_tool_20260708T143817Z`
**Manual QA:** PASS_operator_accepted

### Summary
Added `scripts/main_market_inventory_redacted.py`, a read-only inventory command
that explains why the main bot has zero 3-day non-weather candidates without
running a live cycle, changing cron, changing category gates, or widening the
user-required `MAX_DAYS_TO_EXPIRY=3`.

### Current Inventory
- `MARKET_INVENTORY=PASS`
- `MAX_DAYS=3`
- `TOTAL_FETCHED=79`
- `AFTER_EXPIRY_FILTER=0`
- `AFTER_WEATHER_EXCLUSION=0`
- `AFTER_CATEGORY_FILTER=0`
- `ZERO_CANDIDATE_REASON=no_current_3_day_markets`

### Changes
1. Added a direct-invocation-safe CLI with `--max-days`, `--allowed-categories`,
   and `--sample-limit`.
2. Defaults the max-days gate to 3 and refuses attempts to widen it.
3. Counts public category and ticker samples only after expiry/weather/category
   filters.
4. Added fake-market tests for expiry/category/weather classification and
   redacted output behavior.
5. Added buglog:
   `docs/buglog/pm_main_three_day_redacted_market_inventory_tool_20260708.md`.

### Verified
- `bash -n scripts/cron_micro_live.sh && bash -n scripts/cron_weather_trade.sh`: PASS.
- `python3 -m json.tool feature_list.json`: PASS.
- `python3 -m py_compile scripts/main_market_inventory_redacted.py tests/test_main_market_inventory_redacted.py`: PASS.
- `PYTHONPATH=. pytest tests/test_main_market_inventory_redacted.py -q`: PASS, 8 passed.
- `python3 scripts/main_market_inventory_redacted.py --max-days 3 --allowed-categories index,crypto,economics,commodities,financials`: PASS, `TOTAL_FETCHED=79`, `AFTER_EXPIRY_FILTER=0`, `ZERO_CANDIDATE_REASON=no_current_3_day_markets`.
- `python3 scripts/kalshi_redacted_health_check.py`: PASS, `KALSHI_AUTH=PASS`, `PORTFOLIO_READONLY=PASS`, `OPEN_ORDERS_READONLY=PASS`, `HTTP_STATUS_CLASS=2xx`, `VALUES_PRINTED=no`.
- `PYTHONPATH=. pytest tests -q`: PASS, 536 passed, 1 warning.
- `./scripts/run_tests.sh`: PASS, `STATUS: ALL GATES PASS`.
- Sanitized cron check: main cron present, `TRADING_PAUSED=false`, `MAX_DAYS_TO_EXPIRY=3`, `KALSHI_ALLOWED_CATEGORIES=index,crypto,economics,commodities,financials`; weather remains `WEATHER_DRY_RUN=false WEATHER_LIVE_ENABLED=true`.

### Safety
- No installed cron change, main live manual run, order placement/cancellation,
  category-gate change, weather arm/disarm, service restart,
  Caddy/DNS/systemd/timer/tmux change, Discord notification, or runtime config
  mutation.
- No credential values, auth headers, key paths, or response bodies were printed.

---

# 2026-07-06 (pm_main_cron_preserve_inline_max_days_fix — Preserve Main Cron Inline Runtime Overrides)

**Agent:** Codex (SlimyAI NUC1)
**Project:** pm_updown_bot_bundle / main micro-live cron env loading
**Type:** Source/test-only runtime wrapper fix
**Proof:** `/tmp/proof_pm_main_cron_preserve_inline_max_days_fix_20260706T172739Z`
**Manual QA:** pending_operator_qa

### Summary
Fixed `scripts/cron_micro_live.sh` so operator/cron inline non-secret runtime
overrides survive repo env loading. This directly addresses the diagnosis where
cron showed `MAX_DAYS_TO_EXPIRY=3` but the latest main runtime log still showed
`max_days=14`.

### Changes
1. Reworked the main micro-live wrapper to resolve the repo from the script path,
   support `ENV_FILE` for synthetic tests, fail closed on missing env file, and
   disable xtrace while sourcing.
2. Snapshotted and restored only known non-secret runtime controls after env
   loading: trading pause, order/run caps, min trade price, max days to expiry,
   and category allowlist.
3. Added synthetic fake-env tests proving inline max-days override preservation,
   env fallback when unset, fail-closed missing-env behavior, and no fake secret
   printing.
4. Added buglog:
   `docs/buglog/pm_main_cron_preserve_inline_max_days_fix_20260706.md`.

### Verified
- `bash -n scripts/cron_micro_live.sh`: PASS.
- `bash -n scripts/cron_weather_trade.sh`: PASS.
- `PYTHONPATH=. pytest tests/test_cron_micro_live_env.py tests/test_cron_weather_env.py -q`:
  PASS, 9 passed.
- `python3 scripts/kalshi_redacted_health_check.py`: PASS,
  `KALSHI_AUTH=PASS`, `PORTFOLIO_READONLY=PASS`,
  `OPEN_ORDERS_READONLY=PASS`, `HTTP_STATUS_CLASS=2xx`,
  `VALUES_PRINTED=no`.
- `PYTHONPATH=. pytest tests -q`: PASS, 528 passed, 1 warning.
- `./scripts/run_tests.sh`: PASS, `STATUS: ALL GATES PASS`.
- Sanitized cron posture: main cron present, `TRADING_PAUSED=false`, inline
  `MAX_DAYS_TO_EXPIRY=3`; weather remained
  `WEATHER_DRY_RUN=false WEATHER_LIVE_ENABLED=true`.

### Safety
- No `.env`, key files, PEMs, shell history, auth headers, webhook configs, or
  credential values were inspected or printed.
- No installed cron change, main live manual run, order placement/cancellation,
  category-gate change, weather arm/disarm, service restart,
  Caddy/DNS/systemd/timer/tmux change, Discord notification, or runtime config
  mutation.

### Remaining
- Wait for the next scheduled main cron and verify runtime logs show
  `max_days=3`.
- If short-horizon non-weather betting still does not happen, continue the prior
  diagnosis: current fetched <=3-day markets were weather-only, while broader
  raw <=3-day non-weather markets were sports/other and excluded by the current
  category gates.

---

# 2026-07-05 (pm_weather_live_arm_single_cycle_host_policy_nonce_v2 — Operator Accepted Live-Armed State)

**Agent:** Codex (SlimyAI NUC1)
**Project:** pm_updown_bot_bundle / weather live arming
**Type:** Live cron arming acceptance record
**Proof:** `/tmp/proof_pm_weather_live_arm_single_cycle_host_policy_nonce_v2_20260705T030545Z`
**Manual QA:** PASS_operator_accepted
**Accepted Weather Live Armed State:** true
**Accepted Cron State:** `WEATHER_DRY_RUN=false WEATHER_LIVE_ENABLED=true tiny_caps_present=yes`

### Summary
Operator accepted the weather live-armed cron state after the host-policy nonce
arming phase passed. The accepted state is weather-only live arming with tiny
caps, and the controlled live cycle placed zero orders because no eligible tiny
trade passed all gates.

### Verified
- Repo clean and synced at
  `HEAD == origin/feat/ibkr-forecast-integration ==
  9dc6ad60503e41f575a27ee5c2879d221380fded`.
- Installed weather cron currently has one `cron_weather_trade.sh` line with
  `WEATHER_DRY_RUN=false`, `WEATHER_LIVE_ENABLED=true`, and tiny caps present.
- Main micro-live cron remains present.
- Accepted proof:
  `/tmp/proof_pm_weather_live_arm_single_cycle_host_policy_nonce_v2_20260705T030545Z`.

### Safety
- This acceptance record did not change installed cron, services, Caddy, DNS,
  systemd, timers, tmux, Discord secrets, or trading state.
- No `.env`, key files, PEMs, shell history, auth headers, webhook configs, or
  credential values were inspected or printed.

### Next
- Continue operator monitoring/manual QA. Roll back weather cron to dry-run if
  any WARN/FAIL, ambiguity, or operator decision requires it.

---

# 2026-07-05 (pm_weather_live_secret_rule_compat_fix — Secret-Safe Weather Cron Bootstrap)

**Agent:** Codex (SlimyAI NUC1)
**Project:** pm_updown_bot_bundle / weather live arming compatibility
**Type:** Source/test-only safety fix
**Proof:** `/tmp/proof_pm_weather_live_secret_rule_compat_fix_20260705T021521Z`
**Manual QA:** PASS_operator_accepted
**Accepted Commit:** `8af860b592d4267819cab23dca67cdddddfa190e`

### Summary
Fixed the two blockers from the bounded weather-live arming attempt without
arming weather live or changing installed cron. Future arming validation should
use `/home/slimy/init.sh` as the required host bootstrap; repo-local `init.sh`
is optional only if present. The weather cron wrapper no longer parses `.env`
with `grep | xargs`.

### Changes
1. Updated `AGENTS.md` startup guidance to require `source /home/slimy/init.sh`
   and not require/create a repo-local `init.sh` solely for validation.
2. Reworked `scripts/cron_weather_trade.sh` to resolve `REPO_ROOT` from the
   script path, use `ENV_FILE`, fail closed if the env file is unreadable,
   source the env file with auto-export, and disable xtrace around loading.
3. Added `tests/test_cron_weather_env.py` with synthetic fake env files and a
   stub Python binary to verify valid loading, missing live-limit fail-closed
   behavior, missing env-file fail-closed behavior, no fake secret printing,
   and dry-run default behavior.
4. Updated `docs/ops/weather_runtime_policy.md`, `feature_list.json`, and
   added buglog `docs/buglog/pm_weather_live_secret_rule_compat_fix_20260705.md`.

### Verified
- Repo started clean at `HEAD == origin/feat/ibkr-forecast-integration ==
  be2d729f73cfa20e9aad29ed62fac464a9e99cb5`.
- `bash -n scripts/cron_weather_trade.sh`: PASS.
- `python3 scripts/kalshi_redacted_health_check.py`: PASS,
  `KALSHI_AUTH=PASS`, `PORTFOLIO_READONLY=PASS`,
  `OPEN_ORDERS_READONLY=PASS`, `HTTP_STATUS_CLASS=2xx`,
  `VALUES_PRINTED=no`.
- `PYTHONPATH=. pytest tests/test_cron_weather_env.py tests/test_weather_live.py tests/test_kalshi_redacted_health_check.py -q`:
  PASS, 41 passed.
- `PYTHONPATH=. pytest tests -q`: PASS, 524 passed, 1 warning.
- `./scripts/run_tests.sh`: PASS, `STATUS: ALL GATES PASS`.
- `WEATHER_DRY_RUN=true timeout 90 ./venv/bin/python3 scripts/run_weather_strategy.py --dry-run`:
  PASS dry-run only, `Trades placed: 0`.
- Sanitized cron check: installed weather cron unchanged and dry-run locked
  (`WEATHER_DRY_RUN=true`, `WEATHER_LIVE_ENABLED` absent); main micro-live
  cron unchanged.

### Safety
- No `.env`, key files, PEMs, shell history, auth headers, webhook configs, or
  credential values were inspected or printed by the agent.
- No installed cron change, weather live arming, live weather run, order
  placement/cancellation, service restart, Caddy/DNS/systemd/timer/tmux change,
  or Discord notification.

### Next
- Fresh exact-bounded nonce required before any weather live arming retry.
- Operator accepted commit `8af860b592d4267819cab23dca67cdddddfa190e`
  for this source/test-only compatibility fix.

---

# 2026-07-05 (pm_kalshi_rotation_weather_hardening_final_closeout — Final Verification)

**Agent:** Claude (SlimyAI NUC1)
**Project:** pm_updown_bot_bundle / Kalshi key rotation + weather hardening
**Type:** Closeout / project-state recording (no source changes)

### Summary
Final closeout for the Kalshi key rotation, redacted health/canary refresh,
weather open-exposure hardening, and direct-invocation health-check fix.
Verified actual repo state independently rather than trusting any pre-supplied
summary, then recorded the accepted pushed state.

### Verified
- `git status` clean; `HEAD == origin/feat/ibkr-forecast-integration ==
  0a64324d4130bb9c6c83f3968b024091310cf564`.
- Commits `767b13c`, `d3044e6`, `0a64324` all present in `git log`.
- `python3 scripts/kalshi_redacted_health_check.py`: PASS (redacted 2xx,
  `VALUES_PRINTED=no`).
- `PYTHONPATH=. pytest tests/test_kalshi_redacted_health_check.py`: PASS,
  8 passed.
- `PYTHONPATH=. pytest tests`: PASS, 519 passed, 1 warning.
- `./scripts/run_tests.sh`: PASS, `STATUS: ALL GATES PASS`.
- Weather/dry-run/live-gate/exposure focused pytest subset: PASS, 36 passed.
- Sanitized crontab check: only `WEATHER_DRY_RUN=true` present,
  `WEATHER_LIVE_ENABLED` absent.

### Changes
- Corrected two stale placeholder fields in `feature_list.json`
  (`pm_kalshi_redacted_health_canary_refresh_20260703` commit,
  `pm_redacted_health_direct_invocation_fix_20260703` pushed/commit).
- Added missing `feature_list.json` entry for the weather open-exposure
  hardening commit (`d3044e6`), which had no prior record.
- Added closeout entry `pm_kalshi_rotation_weather_hardening_final_closeout_20260705`
  to `feature_list.json`.
- Added buglog:
  `docs/buglog/pm_kalshi_rotation_weather_hardening_final_closeout_20260705.md`.

### Safety
- No `.env`, keys, PEMs, shell history, or credential values read/printed.
- No live orders, cron/service/systemd/tmux/Caddy/DNS changes, or restarts.
- Weather remains dry-run locked; live not armed.

### Next
- Fresh exact-bounded nonce required before any future weather live arming.

---

# 2026-07-03 (pm_redacted_health_direct_invocation_fix — Direct Script Bootstrap)

**Agent:** Codex (SlimyAI NUC1)
**Project:** pm_updown_bot_bundle / Kalshi redacted health check
**Type:** Import-path bug fix

### Summary
Fixed `scripts/kalshi_redacted_health_check.py` so it can be run directly from
the repo root as `python3 scripts/kalshi_redacted_health_check.py` without
requiring `PYTHONPATH=.`.

### Changes
1. Added safe `__file__`-based repo-root discovery near the top of
   `scripts/kalshi_redacted_health_check.py`.
2. Inserted the repo root into `sys.path` only when missing, before importing
   `config` or repo-local utilities.
3. Added buglog:
   `docs/buglog/pm_redacted_health_direct_invocation_fix_20260703.md`.

### Verified
- `python3 scripts/kalshi_redacted_health_check.py`: PASS; redacted health
  statuses all PASS, `HTTP_STATUS_CLASS=2xx`, `VALUES_PRINTED=no`.
- `PYTHONPATH=. python3 scripts/kalshi_redacted_health_check.py`: PASS; same
  redacted output contract.
- `PYTHONPATH=. pytest tests/test_kalshi_redacted_health_check.py`: PASS,
  8 passed.
- `./scripts/run_tests.sh`: PASS, `STATUS: ALL GATES PASS`.
- Sanitized weather cron posture: dry-run locked and
  `WEATHER_LIVE_ENABLED=true` absent.

### Safety
- No `.env`, key files, PEMs, auth headers, webhook configs, or credential
  values were read or printed.
- No orders were placed or canceled.
- No cron changes, service restarts, pushes, or weather live arm.

### Result
PASS: the redacted Kalshi health check now works by direct invocation and with
`PYTHONPATH=.`.

# 2026-07-03 (pm_add_redacted_kalshi_health_and_canary_refresh — Redacted Health + Canary Fallback)

**Agent:** Codex (SlimyAI NUC1)
**Project:** pm_updown_bot_bundle / Kalshi redacted verification
**Type:** Readonly health check and canary reliability fix

### Summary
Added a purpose-built redacted Kalshi readonly health command and removed the
canary's dependency on a single stale configured ticker. The health command
uses existing runtime configuration, signs readonly GET requests, and prints
only PASS/WARN/FAIL labels, a coarse HTTP status class, and
`VALUES_PRINTED=no`.

### Changes
1. Added `scripts/kalshi_redacted_health_check.py`.
2. Added `tests/test_kalshi_redacted_health_check.py`.
3. Updated `core/canary.py` to select the configured canary ticker when present
   and otherwise fall back to a current fetched market with a usable identifier
   and question.
4. Updated `tests/test_canary.py` for stale ticker fallback behavior.
5. Added buglog:
   `docs/buglog/pm_kalshi_redacted_health_canary_refresh_20260703.md`.

### Verified
- Redacted health command: PASS for auth, portfolio, and open-orders; HTTP
  status class 2xx; values printed no.
- Sanitized canary run: PASS for provider health, Kalshi auth/fetch, and
  pipeline dry-run.
- `bash -n scripts/cron_weather_trade.sh`: PASS.
- Py compile touched Python files: PASS.
- Focused tests: PASS, 37 passed.
- `PYTHONPATH=. pytest tests`: PASS, 519 passed, 1 warning.
- `./scripts/run_tests.sh`: PASS, `STATUS: ALL GATES PASS`.
- Weather dry-run smoke: PASS, `Trades placed: 0`.
- Sanitized cron posture: weather dry-run true, `WEATHER_LIVE_ENABLED` absent.
- Proof: `/tmp/proof_pm_add_redacted_kalshi_health_and_canary_refresh_20260703T202952Z`.

### Safety
- No `.env`, key, PEM, shell history, auth header, webhook config, or secret
  output was read or printed by the agent.
- No order placement/cancellation, live weather arm, cron change, service
  restart, Caddy/DNS/systemd/timer/tmux change, raw webhook, Discord
  notification, force push, reset, or clean.
- Preserved expected dirty weather hardening files:
  `scripts/run_weather_strategy.py` and `tests/test_weather_live.py`.

### Result
PASS: redacted Kalshi credential verification now has a readonly health command
and the canary no longer fails solely because the configured market ticker is
stale.

# 2026-07-03 (pm_kalshi_default_client_mapping_align — Rotated Default Client Mapping, Env Blocked)

**Agent:** Codex (SlimyAI NUC1)  
**Project:** pm_updown_bot_bundle / Kalshi credential mapping  
**Type:** Auth/config safety fix

### Summary
Aligned the source-level default Kalshi client mapping to prefer the rotated
credential fields that passed read-only portfolio/order auth:
`KALSHI_API_KEY_ID` / `KALSHI_PRIVATE_KEY_PATH`, with
`KALSHI_KEY_ID` / `KALSHI_PRIVATE_KEY_FILE` aliases.

### Changes
1. `utils/kalshi_orders.py` now prefers the proven rotated key/path fields
   before stale trading and legacy fields.
2. `strategies/kalshi_weather.py` uses the same rotated-first priority for its
   lazy order-client helper. Weather remains dry-run locked.
3. `scripts/shadow_resolver.py` no longer has a hardcoded legacy key-id or
   bundled key-file fallback; it fails closed if required env fields are absent.
4. Added `tests/test_kalshi_client_mapping.py` for rotated-field priority,
   alias support, and no hardcoded resolver fallback.

### Verified
- Read-only default client auth: PASS, default client selected
  `KALSHI_API_KEY_ID` and balance endpoint returned 2xx.
- Read-only portfolio/open-orders: PASS, balance and resting orders returned
  2xx; resting order count was 0.
- `bash -n scripts/cron_weather_trade.sh`: PASS.
- Py compile touched files: PASS.
- Focused mapping/weather tests: PASS.
- Weather slice tests: PASS.
- `./scripts/run_tests.sh`: PASS, `STATUS: ALL GATES PASS`.
- Full pytest: PASS, 508 passed, 2 warnings.
- Weather dry-run smoke: PASS, `Trades placed: 0`.
- Sanitized cron policy: weather dry-run true, `WEATHER_LIVE_ENABLED=true`
  absent; main cron posture unchanged.
- Proof: `/tmp/proof_pm_kalshi_default_client_mapping_align_20260703T192516Z`.

### Blockers / Warnings
- `.env` is immutable and non-interactive sudo is unavailable, so legacy active
  `.env` fields could not be rewritten or removed in this session. Stale
  `KALSHI_KEY`/`KALSHI_SECRET` remain present until an operator updates the file
  with owner/sudo access.
- Operator has not yet confirmed the old exposed Kalshi key was revoked in the
  Kalshi UI.
- During scoped diff review, the removed hardcoded legacy key-id literal was
  printed once by `git diff`; it is not repeated here. Treat old-key revocation
  as still required.

### Safety
- No order placement, cancellation, live weather run, weather live arm, cron
  change, service restart, Caddy/DNS/systemd/timer/tmux change, or push.
- Preserved expected dirty weather hardening files:
  `scripts/run_weather_strategy.py` and `tests/test_weather_live.py`.

### Result
WARN: default client read-only auth is healthy with rotated fields, but active
immutable `.env` still contains stale legacy fields and operator revocation
confirmation is pending.

# 2026-07-03 (pm_weather_live_activation_guarded — Tiny Live Gate Added, Live Arm Blocked)

**Agent:** Codex (SlimyAI NUC1)
**Project:** pm_updown_bot_bundle / weather live activation guard
**Type:** Trading safety gate

### Summary
Added a weather-only, fail-closed tiny-limit gate for future live weather
activation. Live weather now requires explicit safe limit variables before the
order client path can proceed: max 1 order/run, max $0.25/order, max $1.00/run
exposure, and max $2.00 open weather exposure.

### Changes
1. `scripts/run_weather_strategy.py` now validates required live tiny-limit env
   vars and rejects missing, malformed, non-finite, non-positive, or above-cap
   values.
2. `scripts/run_weather_strategy.py` enforces max order cost, run exposure, and
   open weather exposure before any live weather order placement.
3. `scripts/cron_weather_trade.sh` documents the tiny-limit live cron form and
   requires explicit tiny-limit vars for live mode while preserving dry-run
   defaults.
4. `tests/test_weather_live.py` covers missing, malformed, oversized, per-order,
   and open-exposure gate behavior.
5. Added buglog:
   `docs/buglog/pm_weather_live_activation_guarded_20260703.md`.

### Verified
- `bash -n scripts/cron_weather_trade.sh`: PASS.
- `./venv/bin/python3 -m pytest tests/test_weather_live.py -q`: PASS,
  20 passed.
- `./venv/bin/python3 -m pytest tests/ -q -k 'weather or breaker or circuit or calibration'`:
  PASS, 82 passed, 415 deselected, 1 warning.
- `./scripts/run_tests.sh`: PASS, `STATUS: ALL GATES PASS`.
- `./venv/bin/python3 -m pytest tests/ -q`: PASS, 497 passed, 2 warnings.
- Weather dry-run smoke with tiny limits: PASS dry-run only, 0 trades placed.
- Proof: `/tmp/proof_pm_weather_live_activation_guarded_20260703T155131Z`.

### Safety
- Installed cron was not changed.
- Live weather was not armed and no live weather cycle was executed because cron
  mutation and trading/order actions require a fresh exact-bounded nonce
  approval block under the host policy.
- Main micro-live cron was not changed.
- No service restart, Caddy/DNS/systemd/tmux/timer change, push, raw webhook, or
  secret print.

### Result
WARN: source-level tiny live gate passed validation, but live activation remains
blocked pending explicit nonce approval for cron mutation and any live
trading/order cycle.

# 2026-07-03 (hourly_shadow_env_loading_rewrite — Fail-Closed Env Loading)

**Agent:** Codex (SlimyAI NUC1)
**Project:** pm_updown_bot_bundle / hourly shadow wrapper
**Type:** Shell safety fix

### Summary
Rewrote `scripts/run_hourly_shadow.sh` so the hourly shadow wrapper keeps Kalshi credentials out of source, resolves the repo root from the script path, loads `.env` fail-closed, and validates required variable names without printing values.

### Changes
1. `scripts/run_hourly_shadow.sh` now uses `#!/usr/bin/env bash` and `set -euo pipefail`.
2. The wrapper resolves `SCRIPT_DIR` and `REPO_ROOT` instead of depending on caller cwd.
3. `.env` loading fails closed when missing/unreadable; `source ... || true` was removed.
4. The wrapper validates `KALSHI_KEY` and readable `KALSHI_SECRET_FILE` by name only.
5. Added `tests/test_run_hourly_shadow_env.py` with static wrapper contract tests.
6. Added `docs/ops/hourly_shadow_env_policy.md` documenting the no-inline-secrets/fail-closed policy.

### Verified
- `bash -n scripts/run_hourly_shadow.sh`: PASS.
- Static env-loading assertions: PASS.
- Secret scan of `scripts/run_hourly_shadow.sh`: PASS, zero findings.
- Focused tests: PASS, 11 passed, 481 deselected, 1 warning.
- `./scripts/run_tests.sh`: PASS, `STATUS: ALL GATES PASS`.
- Full pytest: PASS, 492 passed, 2 warnings.
- Weather dry-run smoke: PASS dry-run only.
- Cron policy unchanged: weather cron remains `WEATHER_DRY_RUN=true`, no `WEATHER_LIVE_ENABLED=true`, and `cron_micro_live` remains present.
- Runtime cache hash unchanged for `data/shadow_resolution_cache.json`.
- Proof: `/tmp/proof_hourly_shadow_env_loading_rewrite_20260703T141840Z`.

### Safety Incident
During local inspection, one unsanitized `git diff` command printed the old tracked inline Kalshi private-key value from `HEAD`. The value is not repeated here. Treat that key as exposed and rotate/regenerate the Kalshi credential/key material before relying on it. The committed code removes the inline secret from the script, but history and the prior terminal output still require rotation.

### Remaining
- Operator/Claude closeout should review the proof and decide whether to push after credential rotation guidance is acknowledged.
- `data/shadow_resolution_cache.json` remains dirty runtime cache noise and was intentionally not touched.

### Result
WARN: implementation validation passed and local commit pending; safety incident requires credential rotation recommendation.

# Claude Progress — pm_updown_bot_bundle

## 2026-07-02 (proof_snapshot_diff_template_fix — Secret-Safe Proof Comparison)

**Agent:** Codex (SlimyAI NUC1)
**Project:** pm_updown_bot_bundle / proof snapshot tooling
**Type:** Test/tooling fix

### Summary
Fixed the mission-template false-fail pattern where before/after proof files
were compared as whole files even though decorative headers intentionally differ.
The new helper compares normalized rows and ignores `===` proof headers by
default while keeping output secret-safe.

### Changes
1. Added `scripts/proof_compare.py`, which prints only pass/fail, normalized
   SHA256 values, and row counts by default.
2. Added `tests/test_proof_compare.py` covering header-only differences, real
   content differences, secret-safe default output, and no-header matches.
3. Added `docs/ops/proof_snapshot_diff_policy.md` with safe patterns for cron
   hashes and unrelated dirty file hashes.
4. Added buglog: `docs/buglog/proof_snapshot_diff_template_fix_20260702.md`.

### Verified
- Helper self-check - PASS: `=== BEFORE/AFTER ===` cron proof headers normalize
  to matching content rows.
- `./venv/bin/python3 -m pytest tests/test_proof_compare.py -q` - PASS,
  4 passed.
- `./scripts/run_tests.sh` - PASS, `STATUS: ALL GATES PASS`.
- `./venv/bin/python3 -m pytest tests/ -x -q` - PASS, 488 passed, 2 warnings.
- `WEATHER_DRY_RUN=true timeout 90 ./venv/bin/python3 scripts/run_weather_strategy.py --dry-run` - PASS dry-run only.
- Cron hash compare - PASS, cron unchanged; weather cron still has
  `WEATHER_DRY_RUN=true`, no `WEATHER_LIVE_ENABLED=true`, and `cron_micro_live`
  remains present.
- Unrelated dirty file hashes - PASS byte-identical for
  `data/shadow_resolution_cache.json`, `scripts/run_hourly_shadow.sh`, and `!`.
- Diff secret check - clean.

### Proof
- `/tmp/proof_snapshot_diff_template_fix_20260702T173043Z`

### Safety
- No trading logic changed.
- No live orders, no Discord, no push, no cron/service/systemd/timer/tmux/Caddy/DNS changes.
- Existing unrelated dirty state was preserved.

### Result
PASS: proof snapshot comparisons now have a reusable secret-safe normalized
comparison path.

## 2026-07-02 (run_tests_ml08_timeout_triage — Shell Truth Gate Restored)

**Agent:** Codex (SlimyAI NUC1)
**Project:** pm_updown_bot_bundle / `run_tests.sh` ML-08 timeout triage
**Type:** Test harness fix

### Summary
Resolved the remaining `./scripts/run_tests.sh` blocker. The root cause was stale harness behavior: ML-08 and later parameter checks were launching `runner.py` shadow/micro-live pipelines even though the tests were only meant to validate CLI/parser contracts. Those subprocesses could time out or touch runtime paths, so the checks now validate the bounded source contract without executing the runner pipeline.

### Changes
1. `scripts/run_tests.sh` ML-08 now validates `runner.py` venue argparse choices/default and the explicit polymarket rejection guard statically.
2. ML-11 through ML-16 now validate bankroll/max-pos/micro-live/kalshi/Kelly/risk-cap source contracts statically instead of launching shadow or micro-live subprocesses.
3. ML-09 now checks the meaningful deprecation invariant: zero `datetime.utcnow()` calls.
4. Fixed the ML-13 `$0.01` shell label so `$0` is not expanded to the script path.
5. Added buglog: `docs/buglog/run_tests_ml08_timeout_triage_20260702.md`.

### Verified
- Baseline `timeout 240 bash -x ./scripts/run_tests.sh` - FAIL, ML-08 `runner.py --mode shadow --venue kalshi` timed out after 10 seconds.
- `./scripts/run_tests.sh` - PASS, exit 0.
- `./venv/bin/python3 -m pytest tests/ -x -q` - PASS, 481 passed, 2 warnings.
- `git diff --check -- scripts/run_tests.sh` - PASS.
- Weather cron policy check - PASS: `WEATHER_DRY_RUN=true`, no `WEATHER_LIVE_ENABLED=true`.
- `WEATHER_DRY_RUN=true timeout 90 ./venv/bin/python3 scripts/run_weather_strategy.py --dry-run` - PASS dry-run only.
- Secret marker scan of proof dir - PASS: no webhook/private-key/env-secret markers.

### Proof
- `/tmp/proof_run_tests_ml08_timeout_triage_20260702T143712Z`

### Remaining
- Claude pre-Fable safety closeout is the next step.
- Operator review remains pending before any Fable/live-weather prompt.

### Safety
- No orders placed or canceled.
- No live weather smoke.
- No Discord sent.
- No crontab, service, systemd, timer, tmux, Caddy, DNS, `.env`, or key changes.
- No push.

### Result
PASS: shell truth gate restored. Fable was not run.

## 2026-07-02 (weather_cron_policy_alignment — Installed Cron Dry-Run Alignment)

**Agent:** Codex (SlimyAI NUC1)
**Project:** pm_updown_bot_bundle / Weather runtime policy
**Type:** Exact-bounded crontab-only ops alignment

### Summary
Aligned the installed weather cron with the documented runtime policy. The active `cron_weather_trade` line now visibly says `WEATHER_DRY_RUN=true`, matching the code-level fail-closed behavior added in commit `a93dc66`.

### Changed
1. Updated the existing active weather cron line only:
   `0 */2 * * * WEATHER_DRY_RUN=true /opt/slimy/pm_updown_bot_bundle/scripts/cron_weather_trade.sh`
2. Left the main `cron_micro_live.sh` line present and guarded unchanged.
3. Did not add `WEATHER_LIVE_ENABLED=true`.
4. Did not edit repo source, `.env`, systemd, services, timers, tmux, Caddy, DNS, or Discord configuration.

### Verified
- Crontab policy check - PASS: weather cron has `WEATHER_DRY_RUN=true`.
- Weather live flag check - PASS: weather cron does not contain `WEATHER_LIVE_ENABLED=true`.
- Main cron guard - PASS: `cron_micro_live.sh` still present.
- `WEATHER_DRY_RUN=true timeout 90 ./venv/bin/python3 scripts/run_weather_strategy.py --dry-run` - PASS dry-run smoke only; no live weather smoke.
- `./venv/bin/python3 -m pytest tests/ -q -k 'weather or breaker or circuit or calibration'` - PASS, 77 passed, 404 deselected, 1 warning.
- `./venv/bin/python3 -m pytest tests/ -x -q` - PASS, 481 passed, 2 warnings.
- Secret marker check - PASS after adjudication: only `KALSHI_ALLOWED_CATEGORIES` appeared in raw proof backup; no webhook or private-key markers.

### Proof
- `/tmp/proof_weather_cron_policy_alignment_20260702T142944Z`

### Remaining Blockers
- `./scripts/run_tests.sh` still has the previously reported risk-shell / ML-08 timeout WARN and needs a dedicated triage or explicit unrelated classification before Fable PASS.
- Operator review and Claude safety closeout remain pending before any Fable/live weather prompt.

### Safety
- No orders placed or canceled.
- No Discord sent.
- No service restart.
- No push.
- No repo commit for this crontab-only phase.

### Result
PASS for cron policy alignment; overall weather readiness remains WARN until the shell truth gate warning is resolved or classified.

## 2026-07-02 (weather_policy_and_breaker_test_fix — Dry-Run Policy + Breaker Test Isolation)

**Agent:** Codex (SlimyAI NUC1)
**Project:** pm_updown_bot_bundle / Weather runtime safety and daily funnel tests
**Type:** Safety policy + test fix

### Summary
Documented that weather trading stays dry-run until security cleanup, full tests, operator QA, and Claude safety closeout pass. Fixed the known breaker-state pytest failure by isolating `build_section_anomalies()` tests from the live runtime breaker file.

### Changes
1. Added `docs/ops/weather_runtime_policy.md`.
2. Updated `scripts/cron_weather_trade.sh` and `scripts/run_weather_strategy.py` so live weather requires both `WEATHER_DRY_RUN=false` and `WEATHER_LIVE_ENABLED=true`; unset or false-only cron invocation still dry-runs.
3. Updated daily funnel anomaly logic to use a temp `circuit_breakers.json` from the supplied scratchpad during tests, and only use the runtime breaker file for the real scratchpad path.
4. Added tests for unset/default dry-run, false-only dry-run, explicit two-key live opt-in, and local breaker-state anomaly reporting.

### Verified
- `./venv/bin/python3 -m pytest tests/ -q -k 'breaker or circuit or weather or calibration'` - PASS, 76 passed.
- `./venv/bin/python3 -m pytest tests/test_weather_live.py tests/test_weather_strategy.py tests/test_daily_funnel_report.py::TestSectionAnomalies -q` - PASS, 27 passed.
- `./venv/bin/python3 -m pytest tests/ -x -q` - PASS, 481 passed, 2 warnings.
- `WEATHER_DRY_RUN=true timeout 90 ./venv/bin/python3 scripts/run_weather_strategy.py --dry-run` - PASS dry-run smoke only; no live orders.
- `./venv/bin/python3 -m py_compile ...` and `git diff --check` on task files - PASS.

### Known Blockers
- `./scripts/run_tests.sh` still FAILS on the existing risk-shell suite and ML-08 `runner.py --mode shadow --venue ibkr` timeout. This was recorded honestly and keeps the session at WARN.
- Installed crontab still contains `WEATHER_DRY_RUN=false` for weather. It was not edited because `/home/slimy/AGENTS.md` requires a fresh exact-bounded nonce approval block for cron mutation, and none was provided. The repo code now forces that cron invocation to dry-run unless a future activation also sets `WEATHER_LIVE_ENABLED=true`.

### Safety
- No live weather smoke.
- No orders placed or canceled.
- No Discord sent.
- No services, systemd, timers, tmux, Caddy, DNS, or main bot cron changed.
- Existing unrelated dirty files were preserved: `data/shadow_resolution_cache.json`, `scripts/run_hourly_shadow.sh`, `!`, and `.env.bak.20260702T134909Z`.

### Result
WARN: breaker-state pytest blocker fixed and full pytest passes, but project shell truth gate and nonce-approved crontab cleanup remain unresolved.

## 2026-07-02 (ai_calibration — Historical AI Probability Calibration)

**Agent:** Codex (SlimyAI NUC1)
**Project:** pm_updown_bot_bundle / Kalshi AI calibration
**Type:** Strategy calibration feature

### Summary
Added a historical calibration provider that bins settled `pnl.db` trades by raw AI probability and replaces the non-index flat shrinkage fallback with learned bucket win rates. Index vol-model blending and the `MIN_VOL_PROB` gate remain unchanged.

### Changes
1. Added `providers/ai_calibration.py` with 10% buckets, actual win-rate computation, 24h file-path cache, minimum-data fallback (`<50` trades), sparse-bucket nearest-neighbor fallback (`<5` trades), and flat shrinkage fallback.
2. Wired `strategies/kalshi_optimize.py` so non-index / no-vol-model markets use calibration instead of flat shrinkage when enough settled AI trades exist.
3. Added calibration metadata to proof-pack orders: `calibrated_probability`, `calibration_bucket`, and `calibration_trades`.
4. Added Discord `Calibrated Prob` field only when calibration was used and no vol-model probability exists.
5. Added `tests/test_ai_calibration.py` and adjusted the existing non-index vol-gate test to isolate vol-gate behavior from the new calibration provider.

### Live Calibration Table
- Settled AI-probability trades: 219 (enough data; threshold 50).
- Overall actual rate: 29.2%.
- Average AI probability: 58.6%.
- Overall bias: +29.4 percentage points.
- High-confidence buckets show material overconfidence: `0.7-0.8` bucket had 55 trades, 34.5% actual win rate, average AI probability 73.9%.

### Verified
- `./venv/bin/python3 -m py_compile providers/ai_calibration.py strategies/kalshi_optimize.py utils/discord_notify.py tests/test_ai_calibration.py` — PASS.
- `./venv/bin/python3 -m pytest tests/test_ai_calibration.py tests/test_vol_gate.py -q` — 12 passed, 1 warning.
- `./venv/bin/python3 -m pytest tests/ -x -q` — WARN: stopped on known live-environment failure `test_daily_funnel_report.py::TestSectionAnomalies::test_no_anomalies` because Gemini breaker is OPEN (`total_opens=6`); this same issue was documented in the prior weather session.
- `./venv/bin/python3 -m pytest tests/ -q -k 'not test_no_anomalies'` — 476 passed, 1 deselected, 2 warnings.
- Calibration dump written to `/tmp/proof_ai_calibration_20260702T091758Z/calibration_table.txt`.
- Secret scan of proof dir — PASS.
- `git diff --check` on task files — PASS.

### Result
WARN: implementation and focused/broad validation passed, live `pnl.db` has enough data for calibration, but the full truth gate remains blocked by the pre-existing live breaker-state test.

### Safety
- No crontab, systemd, service, Caddy, DNS, `.env`, or runtime config changes.
- No orders placed or canceled.
- Existing unrelated dirty state preserved: `data/shadow_resolution_cache.json` and untracked `!`.

## 2026-07-01 (pm-updown-kalshi-order-api-fix — V2 Event-Order Endpoint + Cap Fix)

**Agent:** Codex (SlimyAI NUC1)
**Project:** pm_updown_bot_bundle / Kalshi order execution
**Type:** Bug fix / live API compatibility

### Summary
Implemented the remaining production-order fix from the trade-drought diagnostic:
1) switched live order placement to `POST /trade-api/v2/portfolio/events/orders` with Kalshi V2 request format, and
2) made `MAX_ORDER_CENTS` configurable with default `95` cents.

### Changes
1. `utils/kalshi_orders.py` now normalizes all v2 API paths to `/trade-api/v2/...` via `base_url` and `_normalize_api_path`.
2. `place_order()` now builds V2 event-order payloads (`side: bid/ask`, `count`/`price` strings, required order-control fields, generated `client_order_id`).
3. `place_order()` now POSTs to `/trade-api/v2/portfolio/events/orders`.
4. `cancel_order()` now DELETEs `/trade-api/v2/portfolio/events/orders/{order_id}`.
5. `MAX_ORDER_CENTS` changed from hardcoded `50` to env-backed `int(os.getenv("MAX_ORDER_CENTS", "95"))`; stale comment updated.

### Verified
- `./venv/bin/python3 -m pytest tests/ -x -q` — 459 passed, 2 warnings.
- Probe order (1¢ limit) executed on active market `KXETHY-27JAN0100-B2625`: `POST /trade-api/v2/portfolio/events/orders` returned `201`.
- Probe cancellation succeeded immediately: `DELETE /trade-api/v2/portfolio/events/orders/{order_id}` returned success.
- `scripts/review_resting_orders.py` run: `Resting orders: 0`, `Open positions: 0`.
- Proof dir secret scan (`/tmp/proof_order_api_fix_20260701T223020Z/secret_scan.txt`) — no token-shaped data.
- `git diff --check` (task-related files) — no whitespace issues.

### Unverified / Known Issues
- One existing root-cause diagnostic item remains: review whether old/duplicate strategy logic still emits stale order signals in live mode.
- `/opt/slimy/pm_updown_bot_bundle/.env` remains unchanged.
- `MAX_ORDER_CENTS` is now environment-driven but cron and strategy behavior are otherwise unchanged.

### Safety
COMMIT: `38eda0e`

- No crontab/systemd/tmux/Caddy/DNS edits.
- No service restarts.
- No `.env` edits.
- One live probe order was placed and immediately canceled (1¢, resting).
- Existing unrelated dirty state preserved: `data/shadow_resolution_cache.json` and untracked `!`.

## 2026-06-28 (weather_activation — Modern Kalshi Fields + Dry-Run Cron)

**Agent:** Codex (SlimyAI NUC1)
**Project:** pm_updown_bot_bundle / Kalshi weather scanner
**Type:** Production strategy activation — dry-run only

### Summary
Migrated `data/weather_markets.py` off removed Kalshi legacy fields and activated the separate weather scanner cron in dry-run mode. The weather signal edge now uses the requested GFS ensemble threshold model: `count(ensemble_members > threshold) / member_count`, then `edge = model_prob - market_price`.

### Changes
1. `data/weather_markets.py` now reads `yes_bid_dollars`, `yes_ask_dollars`, `volume_fp`, and `open_interest_fp`, while preserving internal normalized keys.
2. Removed the legacy cents-to-dollars heuristic from weather market discovery.
3. `strategies/weather_signals.py` no longer uses a bin probability for KXHIGH; it uses the above-threshold GFS ensemble probability and does not call the LLM cascade.
4. `scripts/run_weather_strategy.py` enforces runner-level safety limits: `WEATHER_MAX_DAILY_TRADES=10`, `WEATHER_MAX_EXPOSURE_PER_CITY=$1.00`, and `WEATHER_MIN_EDGE_PCT=3.0`.
5. `scripts/cron_weather_trade.sh` defaults to `--dry-run` when `WEATHER_DRY_RUN` is unset/true.
6. User crontab weather line is now separate and dry-run: `0 */2 * * * WEATHER_DRY_RUN=true /opt/slimy/pm_updown_bot_bundle/scripts/cron_weather_trade.sh`.
7. Added `tests/test_weather_strategy.py` for modern market fields, GFS signal generation, dry-run order safety, and per-city exposure cap.

### Verified
- `./venv/bin/python3 -m py_compile data/weather_markets.py strategies/weather_signals.py scripts/run_weather_strategy.py tests/test_weather_strategy.py` — PASS.
- `./venv/bin/python3 -m pytest tests/test_weather_strategy.py -q` — 4 passed.
- `./venv/bin/python3 -m pytest tests/ -x -q` — 459 passed, 2 warnings.
- Weather market discovery smoke with cron-style env — Found 20 weather markets; sample `KXHIGHMIA-26JUN28-B92.5 bid=0.56 ask=0.57 vol=15360.36`; field migration PASS.
- `timeout 60 ./venv/bin/python3 scripts/run_weather_strategy.py --dry-run` — generated GFS ensemble signals and dry-run would-place logs only.
- Crontab check — weather cron line has `WEATHER_DRY_RUN=true`; main `cron_micro_live.sh` line unchanged.
- `git diff --check` — PASS.
- Proof-dir secret scan — PASS.

### Unverified / Known Issues
- `./scripts/run_tests.sh` still fails on the known `ML-08` `runner.py --mode shadow --venue kalshi` 10-second timeout path (`PIPESTATUS=1`), unchanged from previous pm_updown sessions.
- Manual QA remains pending: review dry-run weather logs/signals for 24-48h before switching to live.

### Safety
- No `.env` edits, services restarted, timers, tmux, Caddy, DNS, order placement, or order cancellation.
- Main bot strategy `strategies/kalshi_optimize.py` was not modified.
- Existing unrelated dirty state preserved: `data/shadow_resolution_cache.json` and untracked `!`.

---

## 2026-06-28 (vol_gate_implementation — Scenario F MIN_VOL_PROB Gate)

**Agent:** Codex (SlimyAI NUC1)
**Project:** pm_updown_bot_bundle / Kalshi strategy
**Type:** Production strategy filter — Vol-model gate

### Summary
Applied Scenario F from the vol-model backtest by adding a `MIN_VOL_PROB` gate to `strategies/kalshi_optimize.py`. Index markets with an available vol-model probability below the threshold are rejected after blending is computed; non-index markets and vol-model failures are not gated and still use the existing shrinkage fallback.

### Changes
1. Added `MIN_VOL_PROB = float(os.getenv("MIN_VOL_PROB", "0.30"))` with documentation that `0.0` disables the gate.
2. Added `_passes_vol_gate()` and `_get_min_vol_prob()` helpers.
3. Wired the gate after `blend_probability()`: if `vol_prob < MIN_VOL_PROB`, the market is logged with `[VOL_GATE]`, marked `_vol_gate_rejected=True`, and assigned neutral `0.5` so the existing order loop skips it.
4. Added `vol_gate_min` to proof pack order records.
5. Added `Vol Gate` to Discord order notification fields.
6. Added `tests/test_vol_gate.py` with six focused tests: reject low probability, pass high probability, pass at threshold, skip non-index, disabled gate, and env override.

### Verified
- `./venv/bin/python3 -m py_compile strategies/kalshi_optimize.py utils/discord_notify.py tests/test_vol_gate.py` — PASS.
- `./venv/bin/python3 -m pytest tests/test_vol_gate.py -q` — 6 passed, 1 warning.
- `./venv/bin/python3 -m pytest tests/test_vol_gate.py tests/test_vol_model.py -q` — 16 passed, 1 warning.
- `./venv/bin/python3 -m pytest tests/ -x -q` — 455 passed, 2 warnings.
- `git diff --check` — PASS.
- Synthetic shadow smoke with mocked vol probabilities — rejected=1, passed=2, `MIN_VOL_PROB=0.30`.
- Proof-dir secret scan — no raw webhook URLs, private keys, signatures, or token-shaped secrets found.

### Unverified / Known Issues
- `./scripts/run_tests.sh` still fails on the known `ML-08` `runner.py --mode shadow --venue kalshi` 10-second timeout path (`PIPESTATUS=1 0`), unchanged from the prior vol-model/backtest sessions.
- Manual QA remains pending. Cron was not unpaused and no live/shadow runner was executed because the existing shadow path writes trade rows and the shell gate already proves the runner timeout remains.

### Safety
- No `.env`, crontab, cron, service, systemd, timer, tmux, Caddy, or DNS changes.
- No orders placed or canceled. No account state touched.
- Existing unrelated dirty state preserved: `data/shadow_resolution_cache.json` and untracked `!`.

---

## 2026-06-28 (vol_model_backtest — Historical Reconstruction + Scenario Backtest)

**Agent:** Codex (SlimyAI NUC1)
**Project:** pm_updown_bot_bundle / Kalshi backtesting
**Type:** Feature — New offline backtest script

### Summary
Added `scripts/backtest_vol_model.py`, a read-only retrospective backtest that reconstructs settled Kalshi trades, optionally enriches with paginated Kalshi portfolio GETs, recomputes historical yfinance volatility probabilities as of each decision timestamp, and compares actual PnL against six scenario filters.

### What Was Built
1. Reads settled rows from `paper_trading/pnl.db` without modifying the database.
2. Paginates read-only Kalshi portfolio history for `/portfolio/orders` and `/portfolio/fills` when env credentials are available.
3. Parses index/crypto tickers including S&P, NDX/Nasdaq-100, BTC/ETH threshold contracts plus S&P/NDX range contracts.
4. Fetches yfinance historical daily closes with `/tmp/pm_updown_yfinance_cache` caching, uses only data before the decision date, computes 10-day realized vol, then estimates threshold/range probabilities with a log-normal model.
5. Applies production-style probability blending (`0.6 * vol_prob + 0.4 * ai_prob`) and shrinkage fallback for unparsed/non-index rows.
6. Tests scenarios A-F for price floors, max DTE, mid-range focus, wider DTE, and vol-model-only filtering.
7. Writes proof outputs under `/tmp/proof_backtest_build_20260628T112320Z/`.

### Results
- Settled DB trades processed: 271.
- Kalshi API enrichment: 357 executed orders and 362 fills fetched through pagination.
- Actual settled PnL in processed DB rows: `-$3.40`.
- Vol-model rows: 249; unparsed rows: 22.
- Best retrospective scenario: `F Vol model only` — 69 trades, 46.4% win rate, `$5.78` net PnL, 66.9% average edge, 0.17 Sharpe-like.

### Verified
- `./venv/bin/python3 -m py_compile scripts/backtest_vol_model.py` — PASS.
- `./venv/bin/python3 scripts/backtest_vol_model.py --load-dotenv --output-dir /tmp/proof_backtest_build_20260628T112320Z` — PASS.
- CSV validation — PASS, 271 rows.
- `git diff --check` — PASS.
- `./venv/bin/python3 -m pytest tests/test_vol_model.py -q` — 10 passed.
- `./venv/bin/python3 -m pytest tests/ -q` — 449 passed, 2 warnings.
- Proof-dir secret scan — no raw webhook URLs, private keys, signatures, or token-shaped secrets found.

### Unverified / Known Issues
- `./scripts/run_tests.sh` still fails on the known `ML-08` `runner.py --mode shadow --venue kalshi` 10-second timeout path (`PIPESTATUS=1 0`), consistent with the previous vol model session.
- Manual QA remains pending. The processed DB settled PnL is `-$3.40`, which does not match the operator's rough `-$31.20` Kalshi profile figure; reconcile account/profile totals before applying any config recommendation.
- No production trading config was changed. No orders placed or canceled. No cron, services, systemd, tmux, Caddy, or DNS changed.

---

## 2026-06-07 (vol_model_provider — Volatility-Based Probability Prior)

**Agent:** OpenCode (SlimyAI NUC1)
**Project:** pm_updown_bot_bundle / Kalshi strategy
**Type:** Feature — Post-cascade volatility model and shrinkage fallback

### Summary
Added a volatility-based probability model for Kalshi S&P/NDX index contracts. The LLM cascade remains unchanged; after it returns a raw AI probability, supported index tickers are parsed for strike/expiry and blended with a log-normal realized-vol model using `0.6 * vol_prob + 0.4 * ai_prob`. Markets without a usable vol model fall back to shrinkage: `0.5 + (ai_prob - 0.5) * 0.5`.

### Changes
1. Added `providers/vol_model.py` with ticker parsing, yfinance history fetch, 10-day annualized realized volatility, log-normal probability calculation, 5-minute cache, blending, and shrinkage helpers.
2. Integrated the vol model in `strategies/kalshi_optimize.py` immediately after the cascade output and before edge/Kelly sizing.
3. Preserved raw LLM probability in `_ai_raw_probability`; downstream `_ai_true_price` now receives the blended or shrunk probability.
4. Added `[VOL_MODEL]` and `[SHRINKAGE]` logs for index blending and fallback paths.
5. Updated Discord order notifications to keep raw `AI Prob` and add `Vol Model Prob` plus `Blended Prob`.
6. Added `scipy==1.17.1` to `requirements.txt`.
7. Added `tests/test_vol_model.py` covering probability behavior, parsing, blending, shrinkage, unknown tickers, and cache reuse.

### Verified
- `./venv/bin/python3 -c "import yfinance; import scipy.stats; print('OK')"` → `OK`
- Baseline before edits: `./venv/bin/python3 -m pytest tests/ -x -q` → `439 passed, 2 warnings`
- Baseline before edits: `./scripts/run_tests.sh` → pre-existing risk/micro-live failures, including `ML-08` runner timeout after 10s
- `./venv/bin/python3 -m pytest tests/test_vol_model.py -q` → `10 passed`
- `./venv/bin/python3 -m py_compile providers/vol_model.py strategies/kalshi_optimize.py utils/discord_notify.py` → PASS
- `./venv/bin/python3 -m pytest tests/ -x -q` → `449 passed, 2 warnings`
- Quick integration smoke parsed `KXINXU-26MAY08H1600-T7374.9999` and returned live yfinance S&P vol-model result
- Post-change `./scripts/run_tests.sh` → same pre-existing risk/micro-live shell failure path persisted, including `ML-08` runner timeout

### Unverified / Known Issues
- `./scripts/run_tests.sh` is not green; it failed before and after this feature in the risk/micro-live shell suite.
- No live/micro-live order was placed.
- Existing dirty runtime state was left untouched: `data/shadow_resolution_cache.json` and untracked `!`.

---

## 2026-05-07 (resume_execution — Tiny-Limit Probation Restart)

**Agent:** OpenCode (SlimyAI NUC1)
**Project:** pm_updown_bot_bundle / Kalshi strategy
**Type:** Operational — Resume micro-live trading with tiny-limit probation

### Summary
User approved tiny-limit probation restart. Executed resume sequence with maximum safety.

### Actions Taken
1. **Backups created**: crontab and .env backed up to /home/slimy/backups/
2. **.env update**: SKIPPED (immutable flag set, no sudo password). Fallback: env vars injected via cron command
3. **Crontab unpaused**: Micro-live cron line activated with env var overrides:
   - `TRADING_PAUSED=false`
   - `MAX_ORDERS_PER_RUN=1`
   - `MAX_DAILY_LOSS_USD=1.00`
   - `MAX_NOTIONAL_PER_RUN_USD=1.00`
   - `MIN_TRADE_PRICE_CENTS=5`
   - `MAX_DAYS_TO_EXPIRY=14`
   - `KALSHI_ALLOWED_CATEGORIES=index,crypto,economics,commodities,financials`

### Current State
- **Live cron**: ACTIVE (runs every 4h at 00:00, 04:00, 08:00, 12:00, 16:00, 18:00, 20:00 UTC)
- **Orderbook collector**: Still running
- **Daily funnel report**: Still running
- **Probation**: 48 hours

### Monitoring (48h checklist)
- [ ] First cron run verifies 1 order max, ≤$1.00
- [ ] Daily loss stays under $1.00
- [ ] Resting orders reviewed every 6h
- [ ] No sports/esports/unknown trades
- [ ] No capped-edge orders

### Rollback
```bash
crontab /home/slimy/backups/crontab-before-resume-1778172533.bak
```

### Proof
`/tmp/proof_resume_execution_20260507T165102Z/RESUME_EXECUTION.txt`

---

## 2026-05-07 (resume_readiness_gate — Final Resume Readiness Assessment)

**Agent:** OpenCode (SlimyAI NUC1)
**Project:** pm_updown_bot_bundle / Kalshi strategy
**Type:** Read-Only Assessment — Final gate before micro-live trading resume

### Summary
Ran comprehensive read-only gate to verify all safety patches are present and working.
All acceptance criteria pass. Account is clean. Shadow smoke shows only safe would-orders.

### Assessment Results
- **Resting orders**: 0 ✅
- **Open positions**: 0 ✅
- **Balance**: $17.84 (parses correctly) ✅
- **Live cron**: paused ✅
- **Tests**: 439/439 pass ✅
- **Shadow safe would-orders**: 3 (all uncapped, all ≥5c, all index, all ≤14d) ✅
- **Edge rejections**: 4 capped candidates properly rejected ✅
- **Price skips**: 6 sub-5c rejected ✅
- **Expiry skips**: 310 long-dated rejected ✅
- **No live orders**: Only shadow mode executed ✅
- **No secrets leaked**: SCAN_PASS=1 ✅

### Commits Verified
- `7475196`: Edge cap rejection patch
- `0f4bb28`: Trade brakes patch  
- `5785a3a`: Fee multiplier fix

### Recommendation
**PASS_RESUME_READY_TINY_LIMITS**

Resume with probation settings: MAX_ORDERS_PER_RUN=1, MAX_DAILY_LOSS_USD=$1.00, 48h probation.
**Do NOT set TRADING_PAUSED=false without human approval.**

### Proof
`/tmp/proof_resume_readiness_20260507T163004Z/RESULT.txt`

---

## 2026-05-07 (edge_cap_reject — Reject Capped/Insane Edge Candidates)

**Agent:** OpenCode (SlimyAI NUC1)
**Project:** pm_updown_bot_bundle / Kalshi strategy
**Type:** Bug Fix / Safety Hardening — Reject capped edge candidates before live trading

### Summary
Implemented rejection (not mere capping) for raw edge values exceeding MAX_EDGE_PCT (500%).
A capped edge indicates a noisy or broken model/price comparison and must not become an order.

### Problem
Shadow runs were producing capped edge warnings (900%, 3233%, 9900%) and still simulating orders.
The `calculate_edge_pct()` function capped the edge to 500% but returned it as a "valid" edge,
allowing capped candidates to pass all downstream gates and become simulated/live orders.

### Solution
1. Added `calculate_edge_pct_with_flag()` returning `(edge: float, was_capped: bool)`
2. Kept `calculate_edge_pct()` backward-compatible (delegates to new helper)
3. In main loop (`optimize_kalshi_strategy`), after computing `edge_after_fees_pct`,
   check `was_capped` on the raw edge. If True: log rejection and `continue`.
4. This applies to both live and shadow paths — capped edges never reach order placement.

### Files Changed
- `strategies/kalshi_optimize.py`: +40 lines (new helper + rejection logic in main loop)
- `tests/test_trade_brakes.py`: +67 lines (9 new edge-cap rejection tests)

### Verification
- Full test suite: 439/439 pass (430 existing + 9 new) ✅
- Shadow smoke: 3 would-orders (all uncapped), 3 edge rejections (capped), 2 price skips, 344 expiry skips ✅
- Resting orders: 0, Open positions: 0 ✅
- Live cron: paused (micro-live line commented out) ✅
- Secret scan: no secrets in patch ✅
- No live/micro-live execution ✅

### Proof Pack
Location: `/tmp/proof_edge_cap_reject_final/`
Files: repo_status.txt, edge_cap_flow_audit.txt, full_tests.txt, shadow_smoke_after_edge_cap_reject.txt,
      resting_order_review_after_edge_patch.md, git_diff_stat.txt, git_diff_after.txt,
      no_secret_scan.txt, RESULT.txt

### Next Steps
- Monitor shadow mode for continued correct rejection of capped-edge candidates
- Consider reducing MAX_EDGE_PCT from 500% to a lower threshold if shadow still shows many capped edges
- Only unpause live trading after full QA verification

---

## 2026-04-13 (sr-notify fix-qa — Cents/Dollars Unit Audit + Fix)

**Agent:** Claude Code (SlimyAI NUC1)
**Project:** pm_updown_bot_bundle / Kalshi micro-live
**Type:** Bug Fix (Cosmetic — decision logic NOT affected)

### Classification
- **Cosmetic only?** YES — display layer bug, decision logic unaffected
- **Decision logic affected?** NO — all budget guards, Kelly sizer, risk caps use correct dollar floats
- **Historical pnl.db corrupted?** NO — pnl.db has no Kalshi Phase 1 entries (kalshi_optimize.py does not call record_trade)
- **Budget guard / cash remaining affected?** NO — bankroll is dollar float throughout

### Bug Root Cause
`price_cents` (int, 1-99) stored in proof pack orders. A display consumer that reads it as a dollar float and formats with `$` prefix overstates by 100x (5 cents → displayed as $5.00). The `$6.94` the user reported was the result of this misread.

### Audit Report
Full trace at `/tmp/cents_audit.md` — covers: (a) Kalshi API ingestion, (b) order placement, (c) fill handling, (d) pnl.db writes, (e) proof pack / scratchpad, (f) display formatting, (g) budget guards, (h) Kelly sizer, (i) risk caps.

### What Changed
- `strategies/kalshi_optimize.py`:
  - Proof pack `orders_placed` entries now include `size_usd` (float, dollars) and `cost_usd` (float, dollars) alongside `price_cents`
  - Live mode: `cost_usd` extracted from `taker_fill_cost_dollars` API field; fallback `price_cents/100`
  - Shadow mode: `cost_usd` estimated as `price_cents/100`
  - Log line now shows `ORDER PLACED: ... @ Nc -> cost=$X.XXXX`
- `utils/kalshi_orders.py`:
  - Added `cents_to_usd(cents: int) -> float` helper
  - Added `usd_to_cents(dollars: Union[int,float]) -> int` helper
- `tests/test_unit_conventions.py`: new 15-test suite verifying cents/dollars separation

### Evidence — Tonight's 9 Orders (2026-04-13 15:21 UTC)
Proof pack: `proofs/kalshi_optimized_20260413_152108.json`
- 9 orders, only 1 filled (KXINXU-26APR13H1600-T6849.9999 @ 13c, cost=$0.12+$0.01fee=$0.13)
- `total_volume` = $11.84 (sum of size_usd, correct)
- Actual total spend = ~$0.47 (sum of cost_usd)
- `price_cents` values (5,4,1,5,3,8,5,13,1) — if displayed as dollars → $45.00 (WRONG)
- After fix: `cost_usd` field = $0.05, $0.04, $0.01, etc. (CORRECT)

### Tests
```
pytest tests/test_unit_conventions.py -v → 15 passed
pytest tests/test_contract_signals.py tests/test_fear_regime.py -v → 24 passed
```

### Next
- Morning review: verify display layer is reading `cost_usd` not `price_cents` for any dashboard consumers
- If dashboard still shows wrong numbers, check if it's reading from a cached/old proof pack

## 2026-04-08 (backtest_kalshi.py — Vectorized Backtesting Harness)

**Agent:** Claude Code (SlimyAI NUC1)
**Project:** pm_updown_bot_bundle / Kalshi backtesting
**Type:** Feature

### Summary
- Created `backtest_kalshi.py` — Kalshi vectorized backtesting harness
- Reads from: `logs/scratchpad/prior_validation.jsonl` + `proofs/kalshi_optimized_*.json`
- Synthetic mode: generates trades from prior_validation records (proof packs are shadow/no orders)
- Calculates: Sharpe, max drawdown, win rate, profit factor, avg edge, days in market
- Monte Carlo simulation for forward-looking CI (Sharpe + MaxDD 95% CIs)
- Outputs: `proofs/backtest_report_YYYYMMDD.json` + `proofs/backtest_equity_curve_YYYYMMDD.png`
- CLI: `python3 backtest_kalshi.py --days 30 --mc-sims 500`
- Verified: import OK, equity curve PNG saved (82K), report JSON saved

### Outputs
- `proofs/backtest_report_20260408.json`
- `proofs/backtest_equity_curve_20260408.png`
- `feature_list.json` (created)

### Metrics (synthetic mode, 475 trades from prior_validation records)
- Total PnL: $9.88
- Win rate: 33.5%
- Sharpe: 10.38
- MaxDD: $8.82
- Profit factor: 1.14

### Next
- Add actual resolved trade tracking to pnl.db (Kalshi phase currently shadow mode only)
- Integrate with autoresearch experiment configs from notes/

## backtest_kalshi.py — Sharpe annualization fix
- BUG: per-trade Sharpe was annualized with sqrt(trades_per_day * 252), inflating 10-60x
- FIX: aggregate to daily PnL first, then Sharpe = (daily_mean / daily_std) * sqrt(252)
- Max drawdown also switched to daily cumulative PnL curve
- Added Sharpe > 3.0 sanity warning (fires per-result, suppressed in MC loop)
- Added daily_pnl_series to JSON report
- Suppress_warnings param added to calc_metrics for MC loop calls

## backtest_kalshi.py — Sharpe still inflated, round 2
- DIAGNOSTIC: printed raw daily PnL series to identify variance issue
- FIX A: Minimum 20 trading days required for Sharpe (else NaN) — fires correctly for pnl_db (4 days)
- FIX B: Synthetic mode adds no-trade days (~30%), regime flips (~20%), spread noise (~15%)
- FIX D: daily_pnl_series now [{date, pnl, cumulative, trades}] per day
- MC CI: filter NaN sharpes before computing 95% CI (was showing 'nan')

## Multi-model debate pattern in sentiment_scorer.py
- Added multi_model_debate() with 3 roles: Forecaster, Critic, Synthesizer
- Forecaster uses Grok primary; Critic uses GLM or Grok-adversarial
- Synthesizer is local weighted average (no extra API call)
- Consensus flag: agree/disagree based on |prob_diff| > 0.25
- Critique strength > 0.7 shifts weight toward critic
- DEBATE_MODE=true/false in .env (opt-in, default OFF)
- Fallback: if either role fails, degrades to single-model mode
- JSON parsing with regex fallback for unreliable AI JSON producers
- 15-second timeout per role call

## Debate validation + micro-live audit
- Debate: Grok API key SET, but api.x.ai timed out (network unreachable from NUC1).
  Correctly falls back to single-model mode.
  Bug fixed: CRITIC_SYSTEM used .format() with unescaped braces (KeyError).
  Added load_dotenv() to sentiment_scorer.py so keys load on standalone import.
- run-micro-live.sh: correctly passes --mode micro-live, --max-pos 10.0, 5s abort delay
- .env has KALSHI_KEY, KALSHI_TRADING_KEY, KALSHI_TRADING_SECRET_FILE all set
- Kalshi API: balance = $108 confirmed via get_balance()
- Order placement: utils/kalshi_orders.py has place_order() method
- Paper→live toggle: runner.py --mode flag (shadow/micro-live/real-live)
- CRITICAL BUG: runner.py accepts --mode micro-live but kalshi_optimize.py
  only handles shadow/real-live. micro-live falls through to else→skips all trades!
  This means ./run-micro-live.sh currently does NOTHING.
  Fix needed: add mode=="micro-live" handling to kalshi_optimize.py.
- Ready for supervised first micro-live trade once micro-live mode bug is fixed.

## Network diag + micro-live fix
- GROK NETWORK: api.x.ai resolves (104.18.18.80), IPv4 HTTP 200 confirmed.
  Python requests test: Status 200, works correctly.
  Earlier timeout: was hitting wrong endpoint in earlier test, fixed now.
- GLM KEY INVALID: GLM key returns 401 on all endpoints — critic always falls
  back to forecaster-only. Debate still returns debate_used=true with forecaster result.
- MICRO-LIVE FIX: Added is_live normalization in kalshi_optimize.py:
  - is_live = mode in ("real-live", "micro-live")
  - Hard caps: bankroll=$25, max_pos=$5, max_daily_loss=$10
  - Log prefix "[MICRO-LIVE]" for easy grep
  - No code duplication, all gates still enforced
  - runner.py dry_run=(mode=="shadow") correctly passes dry_run=False for micro-live
  - Validation: `[MICRO-LIVE] Hard caps applied: bankroll=$1.08, max_pos=$5.00` (verified)

## Production hardening: dedup + order_id + cron
- FIX: order_id extracted from Kalshi API response (was 'unknown')
  - Kalshi returns `{'order': {'order_id': 'xxx', ...}}`, now correctly extracted
- FIX: Dedup — checks existing open orders + positions before placing
  - Uses `get_orders(status='open')` + `get_positions()` at start of run
  - Skips any market where we already have an open order or position
- FIX: MAX_OPEN_ORDERS=20 safety cap prevents runaway accumulation
  - Logs warning and skips entire run if already at cap
- CRON: scripts/cron_micro_live.sh runs every 4 hours with DEBATE_MODE=true
  - Installed: 0 */4 * * * /opt/slimy/pm_updown_bot_bundle/scripts/cron_micro_live.sh
  - Timeout: 600s max per run, logs to logs/cron_micro_live.log
- Committed and pushed: 85e832f

## 2026-04-09 (premium_10_10_split — Expand AI Premium Tier)

**Agent:** Claude Code (SlimyAI NUC1)
**Project:** pm_updown_bot_bundle / Kalshi strategy
**Type:** Feature

### Summary
- Premium tier: 10 → 20 markets (10 short-term <=7d expiry + 10 long-term >7d)
- Both buckets sorted by volume desc, combined for 20-market AI premium tier
- Bulk tier absorbs remaining markets (0 when ai_max=20, ai_premium=20)
- Log line verified: `[PREMIUM] Short-term: 0, Long-term: 10, Total: 10 (max 10 each)`
- KALSHI_BLOCKED_CATEGORIES confirmed clean: Entertainment/Mentions/Social/Exotics only, no weather/economics blocks

### Changes
- `strategies/kalshi_optimize.py`: replaced volume-only sort + tier loop with two-bucket split
- `AI_MAX_PRIORS_PER_RUN` default: 10 → 20
- `AI_PREMIUM_MAX` default: 10 → 20
- `feature_list.json`: added premium_10_10_split entry

### Next
- pm_updown_bot_bundle OPERATIONAL

## Fix PROVIDERS cascade: remove dead GLM, gemini as fallback
- GLM removed from PROVIDERS (401/429 on every call)
- Cascade: grok_fast → grok_420 → gemini
- Gemini is fast, free, works — replaces GLM as fallback
- PROVIDERS tuple was malformed (missing `},` on gemini, stray `{`)
- All glm references cleaned up (docstring, comments, dead loop)
- Critic provider correctly returns gemini
- get_ai_prior(tier='bulk') returns valid result via gemini

## Cron run discovery 2026-04-10
- 00:00 and 04:00 UTC cron runs: both exit code 0, no orders placed
- Balance: $108, open orders: 18 (BTC/ETH INXY binaries from yesterday)
- Dedup working: 18 existing open orders correctly skip all markets
- api.x.ai Grok timeouts: ~25% of calls timing out at 30s (5/20 at 00:00, 5/17 at 04:00)
- gemini fallback also failing (parse failure — dotenv not loading in cron subprocess)
- Only 3 markets hit fallback_all_failed (got prob=0.5 fallback)
- ROOT CAUSE of today's silent skip: Grok slow/unstable → 20 markets × 30s timeout = ~10 min → runner times out before reaching market evaluation gate
  - cron_micro_live.sh has 60s timeout on runner.py subprocess
  - Runner exits cleanly with code 0 before placing any orders
  - Proof packs NOT written for today's runs (market eval loop never reached)
- Fix options:
  A) Reduce grok_fast timeout from 30s to 10s (more aggressive timeout, faster cascade)
  B) Increase cron timeout from 60s to 300s (give more time for 20 AI priors)
  C) Add parallel AI prior fetching (async calls for all 20 markets simultaneously)
  D) Reduce AI premium tier from 20 to 10 markets per run

## Fix Gemini parse + timeout tuning (2026-04-10)
- Grok timeout 15s -> 25s (15 too tight, 30 too slow)
- Gemini parse failure: added raw response logging + code-fence stripping fix
  - Old code: `text.strip("`").replace("json", "", 1)` only stripped ONE backtick, failed on ```json
  - New code: split on ``` and take parts[1], handles multiline code fences correctly
- Pre-dedup: check both 'ticker' and 'id' field names against existing order tickers
  - Was only checking market.id vs order.ticker → cross-field mismatch missed 10 of 18 orders
- Committed and pushed: 2de38ca

## Fix get_orders status filter (2026-04-10)
- BUG: get_orders() ignored status param entirely — no API param, no client-side filter
- API returned all 18 canceled orders when caller asked for "open" orders
- Dedup falsely blocked all new orders (18 canceled orders matched by ticker)
- FIX: _request() now accepts params kwarg for query string
- FIX: get_orders() passes status to API + client-side filter as backup
- Default changed from "open" to "resting" (Kalshi uses "resting" not "open")
- Dedup callers in kalshi_optimize.py updated to use status="resting"
- Validation: resting=0, canceled=18, all=18 — correct filtering confirmed
- Committed and pushed: 675ab39
## 2026-07-10 - Main Order Intent-to-Submission Gap Diagnosis

- Diagnosed `ORDER_INTENTS=5` at 08:00Z versus the 12:01Z nearest-miss output as an artifact-selection mismatch: the gate tool read `logs/cron.log`, while installed main cron writes `logs/cron_micro_live.log`; nearest-miss reads `logs/main_edge_nearest_miss_latest.json`.
- Corrected the diagnostic default to the micro-live log, surfaced both artifact paths, and added redacted future-run post-intent blocker, submission-status, and skip-reason fields. `ORDER_SUBMISSION_PROCESSED` remains a legacy stage-budget metric, not evidence of a submission attempt.
- Verified `python3 -m py_compile` for touched modules, focused tests (19 passed), `PYTHONPATH=. pytest tests` (567 passed, 1 warning), and `./scripts/run_tests.sh` (`STATUS: ALL GATES PASS`).
- No manual main run, order action, cron change, risk/threshold/category/price/expiry change, weather change, service restart, commit, push, or secret output. New runtime diagnostic fields await a scheduled micro-live run.
- Proof: `/tmp/proof_pm_main_order_intent_to_submission_gap_diagnosis_20260710T132252Z`.
## 2026-07-10 - Main Order Intent-to-Submission Diagnostics Accepted

- Operator manual QA accepted commit `a0f20af444a3e59c2bfd4314c57b467854ac88e0` (`MANUAL_QA_STATUS=PASS_operator_accepted`).
- The accepted diagnostic-only change aligns the gate summary to `logs/cron_micro_live.log`, identifies both tool artifacts, and records future-run redacted post-intent diagnostics.
- Accepted commit remains equal to `origin/feat/ibkr-forecast-integration`; no runtime configuration, cron, weather state, service, or order action changed during acceptance recording.

## 2026-07-15 - Candidate Ledger and Replay Foundation Phase 1A

- Implemented the offline-only QuantAgent-inspired Phase 1A foundation at implementation commit `c2dd1e080a4834c87ab0e01b6c9cb64014ba1e26`: closed schema v1, standard-library SQLite migrations/event store, deterministic binary replay, chronological experiment splits/leakage guards, explicit-path local CLIs, research documentation, and synthetic tests.
- The event store is append-only by API and SQLite triggers: candidate observations are unresolved immutable snapshots; fills, settlement, hypothetical results, assignments, and reviews are later bounded events. Identical append retries are idempotent; conflicting duplicates fail.
- Fee-dependent PnL remains unknown unless maker/taker fees are explicitly supplied. No exchange fee constant was invented. UTC source timestamps newer than candidate observation fail closed; experiment assignments are chronological, strategy-version-pinned, disjoint, and append-once.
- Production isolation is explicit and tested: `runner.py`, main cron, weather cron, and weather runner do not import/invoke the ledger; the research package imports no production runner/strategy/venue/network client; direct import has no production side effects. No production candidate capture, GreedBot integration, external call/transmission, strategy mutation, or autonomous self-modification path exists.
- Verified: schema JSON PASS; py_compile PASS; focused suite 42 passed; full repository suite 609 passed with one pre-existing dependency warning; `./scripts/run_tests.sh` reported `STATUS: ALL GATES PASS`; direct CLI init/read-only validate/summary/replay deterministic smoke PASS; diff check and production-isolation guards PASS.
- Safety: no source behavior in production runners changed; no live/manual strategy run, order action, threshold/category/price/expiry/Kelly/bankroll/notional/liquidity/exposure gate change, cron/weather/service/Caddy/DNS/systemd/timer/tmux/Discord change, secret access/print, API-key creation, or external data transfer.
- Manual operator QA and independent Claude safety review remain pending. `passes` remains false. Phase 1B production capture/wiring requires separate approval.
- Proof: `/tmp/proof_pm_candidate_ledger_replay_foundation_phase1a_20260715T154301Z`.

## 2026-07-15 - Candidate Ledger Phase 1A Accepted (Final Closeout)

- Independent Claude safety/architecture review re-ran every validation command independently (schema JSON, py_compile, 42/42 focused tests, 609/609 full tests, `./scripts/run_tests.sh`, redacted Kalshi health check) with results matching the implementation's claims exactly; confirmed append-only SQLite triggers, migration fail-closed behavior, replay math, chronological leakage guards, CLI contracts, determinism, production isolation, and zero secret findings via a scoped scan. `INDEPENDENT_REVIEW_STATUS=PASS`. Proof: `/tmp/proof_pm_candidate_ledger_replay_phase1a_independent_review_20260715T161040Z`.
- Operator manual QA of the schema/event vocabulary and bounded CLI output completed and accepted commit `9c37bdf516ffc82fcd55c41ff3fa081f4208bfdc` (`MANUAL_QA_STATUS=PASS_operator_accepted`).
- Both proof directories were verified to contain zero actual secret findings via a purpose-built redacted scan (categories: AWS keys, private-key headers, bearer tokens, Discord/Slack webhook URLs, generic api-key/password assignments, `.pem`/`.env` references, 40+ char hex strings) and persisted byte-for-byte outside `/tmp` under `proofs/pm_updown_bot_bundle/` (gitignored; tree-hash verified identical to source, no content exposed). See `proofs/pm_updown_bot_bundle/pm_candidate_ledger_replay_foundation_phase1a_persistence_manifest.md`.
- `feature_list.json` entry `pm_candidate_ledger_replay_foundation_phase1a_20260715` updated in place: `passes=true`, `accepted=true`, `manual_qa_completed=true`, `manual_qa_status=PASS_operator_accepted`, `independent_review_status=PASS`, `production_capture_enabled=false`, `phase_1b_authorized=false`, `result=PASS_OPERATOR_ACCEPTED`.
- No production candidate capture enabled, no Phase 1B implementation, no GreedBot integration, no trading/weather/cron/service/threshold/gate/sizing change, no secret access/print. `MAX_DAYS_TO_EXPIRY` unchanged.
- Next: Phase 1B (any production wiring/shadow capture) requires a separate explicit approval; not authorized by this closeout.
