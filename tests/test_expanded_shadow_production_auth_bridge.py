from __future__ import annotations

import ast
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest
import requests

from scripts import check_expanded_shadow_production_auth_bridge as bridge_checker
from scripts import expanded_shadow_discovery_auth_preflight_redacted as preflight
from scripts import expanded_shadow_discovery_production_auth as launcher
from utils import kalshi
from utils import kalshi_discovery_production_bridge as bridge
from utils.kalshi import (
    DiscoveryOutcome,
    DiscoveryStageCounts,
    KalshiDiscoveryResult,
)


SYNTHETIC_KEY = "SYNTHETIC_DISPOSABLE_IDENTIFIER"
SYNTHETIC_EXCEPTION = "SYNTHETIC_DISPOSABLE_EXCEPTION"
OLD_DIAGNOSTIC_MATERIAL_FIELD = "KALSHI_PRIVATE_KEY_PEM"


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class FakeAuthenticatedClient:
    def __init__(self) -> None:
        self.header_calls = 0
        self.order_calls = 0
        self.capture_calls = 0
        self.cron_calls = 0

    def _get_auth_headers(self, method: str, path: str) -> dict[str, str]:
        assert method == "GET"
        assert path.startswith("/")
        self.header_calls += 1
        return {"SYNTHETIC-AUTH": "REDACTED"}

    def place_order(self, *_args, **_kwargs):
        self.order_calls += 1
        raise AssertionError("order method must not be called")

    def capture(self, *_args, **_kwargs):
        self.capture_calls += 1
        raise AssertionError("capture method must not be called")

    def mutate_cron(self, *_args, **_kwargs):
        self.cron_calls += 1
        raise AssertionError("cron method must not be called")


class CountingFactory:
    def __init__(self, client: object | None = None) -> None:
        self.client = client or FakeAuthenticatedClient()
        self.calls = 0

    def __call__(self) -> object:
        self.calls += 1
        return self.client


def _environment(path: Path) -> dict[str, str]:
    return {
        bridge.PRODUCTION_AUTH_KEY_FIELD: SYNTHETIC_KEY,
        bridge.PRODUCTION_AUTH_PRIVATE_KEY_PATH_FIELD: str(path),
    }


def _secure_synthetic_path(tmp_path: Path) -> Path:
    path = tmp_path / "synthetic-auth-input"
    path.write_text("synthetic", encoding="utf-8")
    path.chmod(0o600)
    return path


def _report(
    path: Path,
    *,
    factory: object = lambda: object(),
    parse_checker=None,
) -> bridge.ProductionAuthReport:
    return bridge.inspect_production_auth(
        _environment(path),
        client_factory=factory,
        parse_checker=parse_checker,
    )


def test_tracked_production_field_contract_is_exact() -> None:
    tree = ast.parse(Path("config.py").read_text(encoding="utf-8"))
    fields = {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
        and node.args[0].value.startswith("KALSHI_")
    }

    assert fields == {
        bridge.PRODUCTION_AUTH_KEY_FIELD,
        bridge.PRODUCTION_AUTH_PRIVATE_KEY_PATH_FIELD,
    }
    assert bridge.REQUIRED_PRODUCTION_AUTH_FIELD_NAMES == (
        "KALSHI_KEY",
        "KALSHI_SECRET_FILE",
    )
    assert bridge.PRIVATE_KEY_SOURCE_TYPE == "PATH"


def test_existing_production_client_factory_is_exact() -> None:
    tree = ast.parse(
        Path("utils/kalshi_discovery_production_bridge.py").read_text(
            encoding="utf-8"
        )
    )
    factory = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "build_production_auth_client"
    )
    calls = {
        node.func.id
        for node in ast.walk(factory)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }

    assert calls == {"KalshiOrderClient", "Path"}


def test_old_diagnostic_material_field_is_not_required_by_bridge() -> None:
    for path in (
        Path("utils/kalshi_redacted_discovery.py"),
        Path("utils/kalshi_discovery_production_bridge.py"),
        Path("scripts/expanded_shadow_discovery_production_auth.py"),
    ):
        assert OLD_DIAGNOSTIC_MATERIAL_FIELD not in path.read_text(
            encoding="utf-8"
        )


def test_missing_production_auth_configuration_fails_preflight() -> None:
    report = bridge.inspect_production_auth({}, client_factory=lambda: object())

    assert report.contract_status == bridge.WARN
    assert report.auth_configuration_present is False
    assert report.private_key_file_exists == bridge.NOT_APPLICABLE


def test_factory_unavailable_fails_preflight(tmp_path: Path) -> None:
    path = _secure_synthetic_path(tmp_path)
    report = bridge.inspect_production_auth(
        _environment(path),
        client_factory=None,
    )

    assert report.contract_status == bridge.FAIL
    assert report.client_factory_available is False


def test_synthetic_path_source_exists(tmp_path: Path) -> None:
    report = _report(_secure_synthetic_path(tmp_path))

    assert report.private_key_file_exists == "yes"


def test_synthetic_path_source_missing(tmp_path: Path) -> None:
    report = _report(tmp_path / "absent-synthetic-input")

    assert report.contract_status == bridge.FAIL
    assert report.private_key_file_exists == "no"


def test_synthetic_permission_check_pass(tmp_path: Path) -> None:
    report = _report(_secure_synthetic_path(tmp_path))

    assert report.private_key_file_permission_status == bridge.PASS


def test_synthetic_permission_check_fail(tmp_path: Path) -> None:
    path = _secure_synthetic_path(tmp_path)
    path.chmod(0o644)
    report = _report(path)

    assert report.private_key_file_permission_status == bridge.FAIL


@pytest.mark.parametrize(
    ("parse_result", "expected"),
    [(True, bridge.PASS), (False, bridge.FAIL)],
)
def test_synthetic_parse_status_matrix(
    tmp_path: Path,
    parse_result: bool,
    expected: str,
) -> None:
    report = _report(
        _secure_synthetic_path(tmp_path),
        parse_checker=lambda _path: parse_result,
    )

    assert report.auth_parse_status == expected


def test_production_default_parse_status_is_not_run(tmp_path: Path) -> None:
    report = _report(_secure_synthetic_path(tmp_path))

    assert report.auth_parse_status == bridge.NOT_RUN
    assert report.contract_status == bridge.PASS


def test_preflight_output_is_exact_field_name_only_contract(
    tmp_path: Path,
    capsys,
) -> None:
    path = _secure_synthetic_path(tmp_path)
    assert preflight.main(
        [],
        environment=_environment(path),
        client_factory=lambda: object(),
    ) == 0
    captured = capsys.readouterr()
    fields = dict(line.split("=", 1) for line in captured.out.splitlines())

    assert tuple(fields) == launcher.PREFLIGHT_OUTPUT_FIELDS
    assert fields == {
        "PRODUCTION_AUTH_CONTRACT": "PASS",
        "AUTH_CLIENT_FACTORY_AVAILABLE": "yes",
        "REQUIRED_AUTH_FIELD_NAMES": "KALSHI_KEY,KALSHI_SECRET_FILE",
        "REQUIRED_AUTH_FIELD_COUNT": "2",
        "AUTH_CONFIGURATION_PRESENT": "yes",
        "PRIVATE_KEY_SOURCE_TYPE": "PATH",
        "PRIVATE_KEY_FILE_EXISTS": "yes",
        "PRIVATE_KEY_FILE_PERMISSION_STATUS": "PASS",
        "AUTH_PARSE_STATUS": "not_run",
        "DIRECT_SECRET_VALUE_OUTPUT": "no",
        "NETWORK_CALL_PERFORMED": "no",
    }
    assert captured.err == ""
    assert SYNTHETIC_KEY not in captured.out
    assert str(path) not in captured.out
    assert "identifier" not in captured.out.lower()
    assert "hash" not in captured.out.lower()


def test_preflight_swallowing_metadata_error_emits_no_exception(
    tmp_path: Path,
    capsys,
) -> None:
    path = _secure_synthetic_path(tmp_path)

    def broken_stat(_path: str):
        raise RuntimeError(SYNTHETIC_EXCEPTION)

    assert preflight.main(
        [],
        environment=_environment(path),
        client_factory=lambda: object(),
        stat_func=broken_stat,
    ) == 1
    captured = capsys.readouterr()

    assert SYNTHETIC_EXCEPTION not in captured.out
    assert SYNTHETIC_EXCEPTION not in captured.err
    assert captured.err == ""


def test_operator_launcher_defaults_to_no_network_or_factory(
    tmp_path: Path,
    capsys,
) -> None:
    path = _secure_synthetic_path(tmp_path)
    factory = CountingFactory()
    request_count = 0

    def forbidden_request(*_args, **_kwargs):
        nonlocal request_count
        request_count += 1
        raise AssertionError("network must not run")

    assert launcher.main(
        [],
        environment=_environment(path),
        client_factory=factory,
        request_get=forbidden_request,
    ) == 0
    captured = capsys.readouterr()

    assert "NETWORK_CALL_PERFORMED=no" in captured.out
    assert factory.calls == 0
    assert request_count == 0


@pytest.mark.parametrize(
    "args",
    [
        ["--api-key"],
        ["--private-key"],
        ["--execute-once", "--again"],
        ["unexpected"],
    ],
)
def test_operator_launcher_requires_exact_one_shot_flag(
    args: list[str],
) -> None:
    assert launcher.main(args, environment={}) == 2


def test_one_shot_mode_invokes_factory_and_discovery_once(
    tmp_path: Path,
    capsys,
) -> None:
    path = _secure_synthetic_path(tmp_path)
    client = FakeAuthenticatedClient()
    factory = CountingFactory(client)
    request_count = 0

    def request_get(*_args, **_kwargs):
        nonlocal request_count
        request_count += 1
        return FakeResponse({"series": []})

    assert launcher.main(
        [launcher.EXECUTE_ONCE_FLAG],
        environment=_environment(path),
        client_factory=factory,
        request_get=request_get,
        timeout_seconds=5,
    ) == 0
    captured = capsys.readouterr()

    assert "DISCOVERY_OUTCOME=SUCCESS_EMPTY" in captured.out
    assert factory.calls == 1
    assert request_count == 1
    assert client.header_calls == 1
    assert client.order_calls == 0
    assert client.capture_calls == 0
    assert client.cron_calls == 0


@pytest.mark.parametrize(
    ("request_behavior", "expected_outcome"),
    [
        (
            lambda: FakeResponse({}, status_code=401),
            DiscoveryOutcome.AUTH_REJECTED,
        ),
        (
            lambda: (_ for _ in ()).throw(requests.exceptions.Timeout()),
            DiscoveryOutcome.NETWORK_TIMEOUT,
        ),
        (
            lambda: FakeResponse({}, status_code=500),
            DiscoveryOutcome.HTTP_ERROR,
        ),
    ],
)
def test_injected_factory_failure_taxonomy(
    tmp_path: Path,
    capsys,
    request_behavior,
    expected_outcome: DiscoveryOutcome,
) -> None:
    path = _secure_synthetic_path(tmp_path)

    def request_get(*_args, **_kwargs):
        return request_behavior()

    assert launcher.main(
        [launcher.EXECUTE_ONCE_FLAG],
        environment=_environment(path),
        client_factory=CountingFactory(),
        request_get=request_get,
    ) == 1
    captured = capsys.readouterr()

    assert f"DISCOVERY_OUTCOME={expected_outcome.value}" in captured.out
    assert captured.err == ""


def test_injected_factory_pagination_error(tmp_path: Path, capsys) -> None:
    path = _secure_synthetic_path(tmp_path)
    calls = 0

    def request_get(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return FakeResponse({"series": [], "cursor": "synthetic-cursor"})

    assert launcher.main(
        [launcher.EXECUTE_ONCE_FLAG],
        environment=_environment(path),
        client_factory=CountingFactory(),
        request_get=request_get,
    ) == 1
    captured = capsys.readouterr()

    assert "DISCOVERY_OUTCOME=PAGINATION_ERROR" in captured.out
    assert calls == 2


@pytest.mark.parametrize(
    "outcome",
    [
        DiscoveryOutcome.SUCCESS_EMPTY,
        DiscoveryOutcome.SUCCESS_NONEMPTY,
        DiscoveryOutcome.AUTH_REJECTED,
        DiscoveryOutcome.NETWORK_TIMEOUT,
        DiscoveryOutcome.HTTP_ERROR,
        DiscoveryOutcome.PAGINATION_ERROR,
    ],
)
def test_production_adapter_preserves_injected_core_outcomes(
    monkeypatch,
    outcome: DiscoveryOutcome,
) -> None:
    factory = CountingFactory()
    core_calls = 0

    def fake_core(**kwargs):
        nonlocal core_calls
        core_calls += 1
        assert callable(kwargs["auth_headers_factory"])
        return KalshiDiscoveryResult(
            outcome=outcome,
            counts=DiscoveryStageCounts(),
        )

    monkeypatch.setattr(
        bridge,
        "_fetch_kalshi_markets_diagnostic_with_auth_headers",
        fake_core,
    )
    result = bridge.ProductionAuthDiscoveryClient(
        client_factory=factory,
        environment={},
    ).discover()

    assert result.outcome is outcome
    assert factory.calls == 1
    assert core_calls == 1


def test_value_based_and_client_header_cores_are_equivalent() -> None:
    def request_get(*_args, **_kwargs):
        return FakeResponse({"series": []})

    legacy = kalshi._fetch_kalshi_markets_diagnostic_authenticated(
        environment={},
        api_key=SYNTHETIC_KEY,
        private_key=object(),
        request_get=request_get,
        header_factory=lambda *_args: {},
    )
    injected = kalshi._fetch_kalshi_markets_diagnostic_with_auth_headers(
        environment={},
        auth_headers_factory=lambda *_args: {},
        request_get=request_get,
    )

    assert injected == legacy


def test_operator_launcher_source_has_no_credential_cli_or_stdin() -> None:
    tree = ast.parse(
        Path("scripts/expanded_shadow_discovery_production_auth.py").read_text(
            encoding="utf-8"
        )
    )
    literals = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    call_leaves = {
        node.func.id
        if isinstance(node.func, ast.Name)
        else node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, (ast.Name, ast.Attribute))
    }

    assert launcher.EXECUTE_ONCE_FLAG in literals
    assert not any("credential" in value.lower() for value in literals)
    assert "input" not in call_leaves
    assert "readline" not in call_leaves


def test_operator_launcher_import_performs_no_network_or_credential_load() -> None:
    code = r'''
import sys

def audit(event, args):
    if event == "open" and args:
        target = str(args[0]).lower()
        if any(marker in target for marker in (".env", ".pem", ".key")):
            raise RuntimeError("forbidden credential load")
    if event.startswith("socket.connect"):
        raise RuntimeError("forbidden network")

sys.addaudithook(audit)
import scripts.expanded_shadow_discovery_production_auth
print("IMPORT_STATUS=PASS")
'''
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        check=False,
        env={
            "PATH": "/usr/bin:/bin",
            "PYTHONPATH": ".",
            "PYTHONDONTWRITEBYTECODE": "1",
        },
    )

    assert completed.returncode == 0
    assert completed.stdout == "IMPORT_STATUS=PASS\n"
    assert completed.stderr == ""


def test_bridge_static_checker_passes_actual_boundary() -> None:
    report = bridge_checker.check_bridge_contract()

    assert report.status == "PASS"
    assert report.violation_rules == ()


def test_runtime_bridge_does_not_connect_to_sqlite(monkeypatch) -> None:
    def forbidden_connect(*_args, **_kwargs):
        raise AssertionError("runtime SQLite access is forbidden")

    monkeypatch.setattr(sqlite3, "connect", forbidden_connect)
    result = bridge.ProductionAuthDiscoveryClient(
        client_factory=CountingFactory(),
        environment={},
        request_get=lambda *_args, **_kwargs: FakeResponse({"series": []}),
    ).discover()

    assert result.outcome is DiscoveryOutcome.SUCCESS_EMPTY


def test_production_auth_paths_are_unchanged_in_legacy_callers() -> None:
    strategy = Path("strategies/kalshi_optimize.py").read_text(encoding="utf-8")
    canary = Path("core/canary.py").read_text(encoding="utf-8")
    runner = Path("runner.py").read_text(encoding="utf-8")

    assert "KalshiOrderClient()" in strategy
    assert "fetch_kalshi_markets()" in strategy
    assert "_check_kalshi_auth_and_fetch" in canary
    assert "run_canary()" in runner
