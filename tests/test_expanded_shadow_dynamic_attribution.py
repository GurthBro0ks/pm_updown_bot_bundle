from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import runpy
import sqlite3
import sys
from zoneinfo import ZoneInfo

import pytest

import research.candidate_ledger.run_observation as observation_module
from research.candidate_ledger.run_observation import (
    CLAIM_STALE_SECONDS,
    DEFAULT_SCHEDULE_GRACE_SECONDS,
    ENV_DYNAMIC_ATTRIBUTION,
    ENV_EXPECTED_AT,
    ENV_RUN_ID,
    ENV_SCHEDULE_GRACE_SECONDS,
    ENV_STATUS_DIR,
    ENV_STATUS_ROOT,
    ExpandedShadowRunObservation,
    UnscheduledInvocationError,
    claim_path,
    derive_natural_run_attribution,
    read_run_status,
    run_id_for_schedule,
    status_path,
)
from scripts.expanded_shadow_run_status_redacted import main as status_main
from strategies import kalshi_optimize
import utils.kalshi as kalshi_api


SLOT = datetime(2026, 7, 21, 16, 0, tzinfo=timezone.utc)
EXPECTED_AT = "2026-07-21T16:00:00Z"
EXPECTED_RUN_ID = "expanded-shadow-20260721T160000Z"


def _dynamic_environment(root: Path, *, grace_seconds: int | None = None) -> dict[str, str]:
    environment = {
        ENV_DYNAMIC_ATTRIBUTION: "true",
        ENV_STATUS_ROOT: str(root),
    }
    if grace_seconds is not None:
        environment[ENV_SCHEDULE_GRACE_SECONDS] = str(grace_seconds)
    return environment


def _dynamic_observation(root: Path, started: datetime) -> ExpandedShadowRunObservation:
    return ExpandedShadowRunObservation.from_environment(
        _dynamic_environment(root),
        now=lambda: started,
    )


def _read(root: Path, *, now: datetime | None = None) -> dict[str, object]:
    return read_run_status(
        status_dir=root,
        expected_run_id=EXPECTED_RUN_ID,
        expected_scheduled_at=EXPECTED_AT,
        now=now or SLOT + timedelta(minutes=5),
        max_status_age_seconds=3600,
        schedule_tolerance_seconds=DEFAULT_SCHEDULE_GRACE_SECONDS,
    )


@pytest.mark.parametrize(
    ("delay", "expected_delay"),
    [
        (timedelta(0), 0),
        (timedelta(seconds=7), 7),
        (timedelta(minutes=8), 480),
        (timedelta(seconds=DEFAULT_SCHEDULE_GRACE_SECONDS), DEFAULT_SCHEDULE_GRACE_SECONDS),
    ],
)
def test_exact_and_in_grace_invocations_derive_the_same_slot(
    delay: timedelta,
    expected_delay: int,
) -> None:
    attribution = derive_natural_run_attribution(SLOT + delay)

    assert attribution.expected_scheduled_at == EXPECTED_AT
    assert attribution.run_id == EXPECTED_RUN_ID
    assert attribution.delay_seconds == expected_delay


def test_invocation_just_beyond_grace_is_unscheduled() -> None:
    with pytest.raises(UnscheduledInvocationError):
        derive_natural_run_attribution(
            SLOT + timedelta(seconds=DEFAULT_SCHEDULE_GRACE_SECONDS + 1)
        )


def test_invocation_immediately_before_slot_is_not_rounded_into_future() -> None:
    with pytest.raises(UnscheduledInvocationError):
        derive_natural_run_attribution(SLOT - timedelta(seconds=1))


def test_odd_hour_invocation_is_unscheduled() -> None:
    with pytest.raises(UnscheduledInvocationError):
        derive_natural_run_attribution(SLOT + timedelta(hours=1))


def test_schedule_derivation_requires_timezone_and_bounded_grace() -> None:
    with pytest.raises(ValueError):
        derive_natural_run_attribution(datetime(2026, 7, 21, 16, 0))
    with pytest.raises(ValueError):
        derive_natural_run_attribution(SLOT, grace_seconds=1801)


def test_timezone_offsets_do_not_change_utc_attribution() -> None:
    utc_start = SLOT + timedelta(minutes=5)
    offset_start = utc_start.astimezone(timezone(timedelta(hours=9)))

    assert derive_natural_run_attribution(utc_start) == derive_natural_run_attribution(
        offset_start
    )


@pytest.mark.parametrize(
    "utc_start",
    [
        datetime(2026, 3, 8, 8, 5, tzinfo=timezone.utc),
        datetime(2026, 11, 1, 6, 5, tzinfo=timezone.utc),
    ],
)
def test_dst_transition_dates_do_not_affect_utc_derivation(utc_start: datetime) -> None:
    local_start = utc_start.astimezone(ZoneInfo("America/New_York"))

    assert derive_natural_run_attribution(local_start) == derive_natural_run_attribution(
        utc_start
    )


def test_run_id_requires_an_exact_even_hour_utc_slot() -> None:
    assert run_id_for_schedule(SLOT) == EXPECTED_RUN_ID
    with pytest.raises(ValueError):
        run_id_for_schedule(SLOT + timedelta(hours=1))
    with pytest.raises(ValueError):
        run_id_for_schedule(SLOT + timedelta(seconds=1))


def test_dynamic_status_root_and_path_are_stable(tmp_path: Path) -> None:
    first = _dynamic_observation(tmp_path, SLOT + timedelta(seconds=2))

    assert first.enabled
    assert first.output_path == tmp_path / f"{EXPECTED_RUN_ID}.json"
    assert first.claim_file == tmp_path / f"{EXPECTED_RUN_ID}.claim"
    assert status_path(tmp_path, first.run_id) == first.output_path
    assert claim_path(tmp_path, first.run_id) == first.claim_file


def test_consecutive_natural_runs_use_distinct_ids_in_one_root(tmp_path: Path) -> None:
    observed_ids = []
    for hour in (14, 16, 18):
        started = datetime(2026, 7, 21, hour, 0, 3, tzinfo=timezone.utc)
        observation = _dynamic_observation(tmp_path, started)
        assert observation.enabled
        assert observation.complete(exit_code=0, candidates_processed=0, total_markets=0)
        observed_ids.append(observation.run_id)

    assert observed_ids == [
        "expanded-shadow-20260721T140000Z",
        "expanded-shadow-20260721T160000Z",
        "expanded-shadow-20260721T180000Z",
    ]
    assert len(list(tmp_path.glob("*.json"))) == 3


def test_duplicate_running_invocation_is_blocked(tmp_path: Path) -> None:
    first = _dynamic_observation(tmp_path, SLOT + timedelta(seconds=1))
    duplicate = _dynamic_observation(tmp_path, SLOT + timedelta(seconds=2))

    assert first.enabled
    assert not duplicate.enabled
    assert duplicate.blocks_scanner
    assert duplicate.configuration_status == "DUPLICATE_RUNNING"
    assert _read(tmp_path)["EXPANDED_SHADOW_RUN_STATUS"] == "RUNNING"


def test_duplicate_after_completion_cannot_overwrite_success(tmp_path: Path) -> None:
    first = _dynamic_observation(tmp_path, SLOT + timedelta(seconds=1))
    assert first.complete(exit_code=0, candidates_processed=0, total_markets=0)
    original = status_path(tmp_path, EXPECTED_RUN_ID).read_bytes()

    duplicate = _dynamic_observation(tmp_path, SLOT + timedelta(seconds=2))

    assert duplicate.blocks_scanner
    assert duplicate.configuration_status == "DUPLICATE_COMPLETED"
    assert status_path(tmp_path, EXPECTED_RUN_ID).read_bytes() == original
    assert _read(tmp_path)["EXPANDED_SHADOW_RUN_STATUS"] == "PASS"


def test_duplicate_after_failure_preserves_failure(tmp_path: Path) -> None:
    first = _dynamic_observation(tmp_path, SLOT + timedelta(seconds=1))
    assert first.fail(RuntimeError("synthetic redacted failure"))
    original = status_path(tmp_path, EXPECTED_RUN_ID).read_bytes()

    duplicate = _dynamic_observation(tmp_path, SLOT + timedelta(seconds=2))

    assert duplicate.blocks_scanner
    assert duplicate.configuration_status == "DUPLICATE_FAILED"
    assert status_path(tmp_path, EXPECTED_RUN_ID).read_bytes() == original
    assert _read(tmp_path)["EXPANDED_SHADOW_RUN_STATUS"] == "FAIL"


def test_concurrent_duplicate_claim_allows_exactly_one_owner(tmp_path: Path) -> None:
    started = SLOT + timedelta(seconds=1)

    with ThreadPoolExecutor(max_workers=2) as executor:
        observations = list(
            executor.map(lambda _unused: _dynamic_observation(tmp_path, started), range(2))
        )

    assert sum(observation.enabled for observation in observations) == 1
    blocked = [observation for observation in observations if observation.blocks_scanner]
    assert len(blocked) == 1
    assert blocked[0].configuration_status == "DUPLICATE_RUNNING"


def test_stale_claim_fails_closed_without_deletion(tmp_path: Path) -> None:
    stale = claim_path(tmp_path, EXPECTED_RUN_ID)
    stale.write_bytes(b"")
    stale_time = (SLOT - timedelta(seconds=CLAIM_STALE_SECONDS + 1)).timestamp()
    os.utime(stale, (stale_time, stale_time))

    observation = _dynamic_observation(tmp_path, SLOT)

    assert observation.blocks_scanner
    assert observation.configuration_status == "STALE_CLAIM"
    assert stale.is_file()
    assert not status_path(tmp_path, EXPECTED_RUN_ID).exists()


def test_malformed_existing_status_fails_closed_without_overwrite(tmp_path: Path) -> None:
    malformed = status_path(tmp_path, EXPECTED_RUN_ID)
    malformed.write_text("not-json", encoding="utf-8")

    observation = _dynamic_observation(tmp_path, SLOT)

    assert observation.blocks_scanner
    assert observation.configuration_status == "MALFORMED_EXISTING_STATUS"
    assert malformed.read_text(encoding="utf-8") == "not-json"


def test_dynamic_attribution_disabled_preserves_default_behavior(tmp_path: Path) -> None:
    observation = ExpandedShadowRunObservation.from_environment(
        {
            ENV_DYNAMIC_ATTRIBUTION: "false",
            ENV_STATUS_ROOT: str(tmp_path),
        },
        now=lambda: SLOT + timedelta(hours=1),
    )

    assert not observation.enabled
    assert not observation.blocks_scanner
    assert observation.configuration_warning_count == 0
    assert list(tmp_path.iterdir()) == []


def test_explicit_synthetic_override_remains_available(tmp_path: Path) -> None:
    observation = ExpandedShadowRunObservation.from_environment(
        {
            ENV_RUN_ID: "synthetic-test-run",
            ENV_EXPECTED_AT: "2026-07-21T15:17:00Z",
            ENV_STATUS_DIR: str(tmp_path),
        },
        now=lambda: datetime(2026, 7, 21, 15, 17, 1, tzinfo=timezone.utc),
    )

    assert observation.enabled
    assert observation.run_id == "synthetic-test-run"
    assert observation.expected_scheduled_at == "2026-07-21T15:17:00Z"


@pytest.mark.parametrize(
    ("started", "status"),
    [
        (SLOT - timedelta(seconds=1), "UNSCHEDULED"),
        (SLOT + timedelta(minutes=15, seconds=1), "UNSCHEDULED"),
        (SLOT + timedelta(hours=1), "UNSCHEDULED"),
    ],
)
def test_unscheduled_dynamic_invocations_block_scanner(
    tmp_path: Path,
    started: datetime,
    status: str,
) -> None:
    observation = _dynamic_observation(tmp_path, started)

    assert not observation.enabled
    assert observation.blocks_scanner
    assert observation.configuration_status == status
    assert list(tmp_path.iterdir()) == []


def test_dynamic_grace_is_configurable_within_bound(tmp_path: Path) -> None:
    observation = ExpandedShadowRunObservation.from_environment(
        _dynamic_environment(tmp_path, grace_seconds=60),
        now=lambda: SLOT + timedelta(seconds=61),
    )

    assert observation.blocks_scanner
    assert observation.configuration_status == "UNSCHEDULED"


def test_ambiguous_dynamic_and_explicit_attribution_fails_closed(tmp_path: Path) -> None:
    environment = _dynamic_environment(tmp_path)
    environment[ENV_RUN_ID] = EXPECTED_RUN_ID

    observation = ExpandedShadowRunObservation.from_environment(
        environment,
        now=lambda: SLOT,
    )

    assert observation.blocks_scanner
    assert observation.configuration_status == "AMBIGUOUS_DYNAMIC_CONFIGURATION"


def test_capture_disabled_run_is_representable_as_success(tmp_path: Path) -> None:
    observation = _dynamic_observation(tmp_path, SLOT + timedelta(seconds=1))
    assert observation.complete(exit_code=0, candidates_processed=0, total_markets=0)

    status = _read(tmp_path)
    assert status["EXPANDED_SHADOW_RUN_STATUS"] == "PASS"
    assert status["CAPTURE_MODE"] == "disabled"


def test_wrong_schedule_record_is_rejected(tmp_path: Path) -> None:
    observation = _dynamic_observation(tmp_path, SLOT + timedelta(seconds=1))
    assert observation.complete(exit_code=0, candidates_processed=0, total_markets=0)
    path = status_path(tmp_path, EXPECTED_RUN_ID)
    record = json.loads(path.read_text(encoding="utf-8"))
    record["expected_scheduled_at"] = "2026-07-21T14:00:00Z"
    path.write_text(json.dumps(record), encoding="utf-8")

    status = _read(tmp_path)
    assert status["EXPANDED_SHADOW_RUN_STATUS"] == "FAIL"
    assert status["STALE_STATUS_REJECTED"] == "yes"


def test_failed_terminal_write_leaves_atomic_started_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observation = _dynamic_observation(tmp_path, SLOT + timedelta(seconds=1))

    monkeypatch.setattr(
        observation_module.os,
        "replace",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("synthetic replace failure")),
    )
    assert not observation.complete(exit_code=0, candidates_processed=0, total_markets=0)

    status = _read(tmp_path)
    assert status["EXPANDED_SHADOW_RUN_STATUS"] == "RUNNING"


def test_observation_path_never_uses_sqlite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sqlite3,
        "connect",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("runtime SQLite access is forbidden")
        ),
    )

    observation = _dynamic_observation(tmp_path, SLOT + timedelta(seconds=1))
    assert observation.complete(exit_code=0, candidates_processed=0, total_markets=0)
    assert _read(tmp_path)["RUNTIME_SQLITE_ACCESS"] == "none"


def test_dynamic_observer_derives_one_exact_lookup_and_exits(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    observation = _dynamic_observation(tmp_path, SLOT + timedelta(seconds=1))
    assert observation.complete(exit_code=0, candidates_processed=0, total_markets=0)

    result = status_main(
        [
            "--status-root",
            str(tmp_path),
            "--now",
            "2026-07-21T16:15:00Z",
        ]
    )
    output = capsys.readouterr().out

    assert result == 0
    assert f"RUN_ID={EXPECTED_RUN_ID}" in output
    assert f"EXPECTED_SCHEDULED_AT={EXPECTED_AT}" in output
    assert len(output.splitlines()) == 23


def test_dynamic_observer_rejects_late_unscheduled_lookup(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exc_info:
        status_main(
            [
                "--status-root",
                str(tmp_path),
                "--slot-lookup-window-seconds",
                "900",
                "--now",
                "2026-07-21T16:15:01Z",
            ]
        )

    assert exc_info.value.code == 2


def test_blocking_attribution_contract_exits_before_scanner_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    strategy_path = Path(kalshi_optimize.__file__).resolve()

    class BlockedObservation:
        configuration_warning_count = 1
        configuration_status = "UNSCHEDULED"
        blocks_scanner = True

    monkeypatch.setattr(
        ExpandedShadowRunObservation,
        "from_environment",
        classmethod(lambda cls: BlockedObservation()),
    )
    monkeypatch.setattr(
        kalshi_api,
        "fetch_kalshi_markets",
        lambda: (_ for _ in ()).throw(AssertionError("scanner must not run")),
    )
    monkeypatch.setattr(sys, "argv", [str(strategy_path), "--mode", "shadow"])

    with pytest.raises(SystemExit) as exc_info:
        runpy.run_path(str(strategy_path), run_name="__main__")

    assert exc_info.value.code == 2
