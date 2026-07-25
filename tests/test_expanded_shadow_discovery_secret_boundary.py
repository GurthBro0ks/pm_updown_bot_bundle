from __future__ import annotations

import ast
import builtins
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from utils import kalshi
from scripts import check_expanded_shadow_discovery_secret_boundary as checker
from scripts import expanded_shadow_discovery_auth_preflight_redacted as preflight
from scripts import expanded_shadow_discovery_redacted as cli
from utils.kalshi import (
    DiscoveryOutcome,
    DiscoveryStageCounts,
    KalshiDiscoveryResult,
)
from utils.kalshi_redacted_discovery import (
    AUTH_KEY_FIELD,
    AUTH_PRIVATE_KEY_MATERIAL_FIELD,
    FAIL,
    InheritedEnvironmentDiscoveryClient,
    PASS,
    REQUIRED_AUTH_FIELD_NAMES,
    WARN,
    inspect_inherited_auth,
)


SYNTHETIC_KEY = "SYNTHETIC_DISPOSABLE_KEY_IDENTIFIER"
SYNTHETIC_PRIVATE_MATERIAL = "SYNTHETIC_DISPOSABLE_PRIVATE_MATERIAL"
SYNTHETIC_EXCEPTION = "SYNTHETIC_DISPOSABLE_EXCEPTION_TEXT"
SYNTHETIC_RECORD = "SYNTHETIC_DISPOSABLE_RECORD_IDENTIFIER"


class FakePrivateKey:
    pass


class FakeClient:
    def __init__(
        self,
        result: KalshiDiscoveryResult | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.calls = 0

    def discover(self) -> KalshiDiscoveryResult:
        self.calls += 1
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload


def _loader(_material: str) -> object:
    return FakePrivateKey()


def _invalid_loader(_material: str) -> object:
    raise ValueError(SYNTHETIC_EXCEPTION)


def _environment(**overrides: str) -> dict[str, str]:
    values = {
        AUTH_KEY_FIELD: SYNTHETIC_KEY,
        AUTH_PRIVATE_KEY_MATERIAL_FIELD: SYNTHETIC_PRIVATE_MATERIAL,
    }
    values.update(overrides)
    return values


def _result(outcome: DiscoveryOutcome) -> KalshiDiscoveryResult:
    success = outcome in {
        DiscoveryOutcome.SUCCESS_EMPTY,
        DiscoveryOutcome.SUCCESS_NONEMPTY,
    }
    nonempty = outcome is DiscoveryOutcome.SUCCESS_NONEMPTY
    return KalshiDiscoveryResult(
        outcome=outcome,
        markets=(
            (
                {
                    "ticker": SYNTHETIC_RECORD,
                    "title": SYNTHETIC_RECORD,
                    "event_name": SYNTHETIC_RECORD,
                },
            )
            if nonempty
            else ()
        ),
        counts=DiscoveryStageCounts(
            request_attempted=1 if outcome is not DiscoveryOutcome.AUTH_CONFIGURATION_MISSING else 0,
            page_count=2 if success else 0,
            raw_record_count=1 if nonempty else 0 if success else None,
            parsed_record_count=1 if nonempty else 0 if success else None,
            active_record_count=1 if nonempty else 0 if success else None,
            category_eligible_count=1 if nonempty else 0 if success else None,
            expiry_eligible_count=1 if nonempty else 0 if success else None,
            price_liquidity_eligible_count=1 if nonempty else 0 if success else None,
            final_eligible_count=1 if nonempty else 0 if success else None,
        ),
        request_status_class="2XX" if success else "4XX",
    )


@pytest.mark.parametrize(
    ("environment", "expected_missing"),
    [
        ({}, 2),
        ({AUTH_KEY_FIELD: SYNTHETIC_KEY}, 1),
        ({AUTH_PRIVATE_KEY_MATERIAL_FIELD: SYNTHETIC_PRIVATE_MATERIAL}, 1),
        ({AUTH_KEY_FIELD: "", AUTH_PRIVATE_KEY_MATERIAL_FIELD: ""}, 2),
        ({AUTH_KEY_FIELD: " ", AUTH_PRIVATE_KEY_MATERIAL_FIELD: "\t"}, 2),
    ],
)
def test_missing_inherited_auth_matrix_fails_closed(
    environment: dict[str, str],
    expected_missing: int,
) -> None:
    report = inspect_inherited_auth(environment, private_key_loader=_loader)

    assert report.runtime_context == WARN
    assert report.parse_status == WARN
    assert report.missing_field_count == expected_missing


def test_malformed_inherited_key_material_fails_closed() -> None:
    report = inspect_inherited_auth(
        _environment(),
        private_key_loader=_invalid_loader,
    )

    assert report.runtime_context == FAIL
    assert report.parse_status == FAIL
    assert report.missing_field_count == 0


def test_all_synthetic_fields_present_passes_without_network() -> None:
    report = inspect_inherited_auth(
        _environment(),
        private_key_loader=_loader,
    )

    assert report.runtime_context == PASS
    assert report.parse_status == PASS
    assert report.missing_field_count == 0


@pytest.mark.parametrize(
    "outcome",
    [
        DiscoveryOutcome.SUCCESS_EMPTY,
        DiscoveryOutcome.SUCCESS_NONEMPTY,
        DiscoveryOutcome.AUTH_REJECTED,
        DiscoveryOutcome.NETWORK_TIMEOUT,
        DiscoveryOutcome.NETWORK_ERROR,
        DiscoveryOutcome.HTTP_ERROR,
        DiscoveryOutcome.JSON_PARSE_ERROR,
        DiscoveryOutcome.SCHEMA_ERROR,
        DiscoveryOutcome.PAGINATION_ERROR,
        DiscoveryOutcome.INTERNAL_DISCOVERY_ERROR,
    ],
)
def test_injected_client_preserves_every_discovery_outcome(
    outcome: DiscoveryOutcome,
    capsys,
) -> None:
    client = FakeClient(_result(outcome))
    exit_code = cli.main([], client=client)
    captured = capsys.readouterr()

    assert client.calls == 1
    assert f"DISCOVERY_OUTCOME={outcome.value}" in captured.out
    assert captured.err == ""
    assert exit_code == (
        0
        if outcome in {DiscoveryOutcome.SUCCESS_EMPTY, DiscoveryOutcome.SUCCESS_NONEMPTY}
        else 1
    )


@pytest.mark.parametrize(
    ("environment", "loader"),
    [
        ({}, _loader),
        (_environment(), _invalid_loader),
    ],
)
def test_auth_failure_performs_zero_network_calls(
    environment: dict[str, str],
    loader,
) -> None:
    request_count = 0

    def request_get(*_args, **_kwargs):
        nonlocal request_count
        request_count += 1
        raise AssertionError("synthetic request must not occur")

    result = InheritedEnvironmentDiscoveryClient(
        environment=environment,
        private_key_loader=loader,
        request_get=request_get,
    ).discover()

    assert result.outcome is DiscoveryOutcome.AUTH_CONFIGURATION_MISSING
    assert result.counts.request_attempted == 0
    assert request_count == 0


def test_valid_inherited_auth_uses_authenticated_core_without_file_access(
    monkeypatch,
) -> None:
    responses = [FakeResponse({"series": []})]
    request_count = 0

    def request_get(*_args, **_kwargs):
        nonlocal request_count
        request_count += 1
        return responses.pop(0)

    def forbidden_open(*_args, **_kwargs):
        raise AssertionError("file access is forbidden")

    monkeypatch.setattr(builtins, "open", forbidden_open)
    result = InheritedEnvironmentDiscoveryClient(
        environment=_environment(),
        private_key_loader=_loader,
        request_get=request_get,
        header_factory=lambda *_args: {},
    ).discover()

    assert result.outcome is DiscoveryOutcome.SUCCESS_EMPTY
    assert result.counts.request_attempted == 1
    assert request_count == 1


def test_inherited_client_preserves_endpoint_and_header_factory_semantics() -> None:
    header_calls: list[tuple[str, str, str, object]] = []
    request_calls: list[tuple[str, dict, dict]] = []
    fake_private_key = FakePrivateKey()

    def private_key_loader(_material: str) -> object:
        return fake_private_key

    def header_factory(method, path, api_key, private_key):
        header_calls.append((method, path, api_key, private_key))
        return {"SYNTHETIC": "REDACTED"}

    def request_get(url, *, headers, params, timeout):
        request_calls.append((url, headers, params))
        assert timeout == 15
        return FakeResponse({"series": []})

    result = InheritedEnvironmentDiscoveryClient(
        environment=_environment(),
        private_key_loader=private_key_loader,
        request_get=request_get,
        header_factory=header_factory,
    ).discover()

    assert result.outcome is DiscoveryOutcome.SUCCESS_EMPTY
    assert header_calls == [("GET", "/series", SYNTHETIC_KEY, fake_private_key)]
    assert len(request_calls) == 1
    assert request_calls[0][0].endswith("/trade-api/v2/series")
    assert request_calls[0][1] == {"SYNTHETIC": "REDACTED"}
    assert request_calls[0][2] == {"include_volume": "true"}


def test_authenticated_core_result_matches_legacy_injected_path() -> None:
    def request_get(*_args, **_kwargs):
        return FakeResponse({"series": []})

    legacy = kalshi.fetch_kalshi_markets_diagnostic(
        environment={AUTH_KEY_FIELD: SYNTHETIC_KEY},
        private_key=FakePrivateKey(),
        request_get=request_get,
        header_factory=lambda *_args: {},
    )
    isolated = InheritedEnvironmentDiscoveryClient(
        environment=_environment(),
        private_key_loader=_loader,
        request_get=request_get,
        header_factory=lambda *_args: {},
    ).discover()

    assert isolated == legacy


def test_legacy_wrapper_and_direct_shadow_caller_remain_on_established_path() -> None:
    kalshi_tree = ast.parse(Path(kalshi.__file__).read_text(encoding="utf-8"))
    wrapper = next(
        node
        for node in kalshi_tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "fetch_kalshi_markets_diagnostic"
    )
    call_names = {
        node.func.id
        for node in ast.walk(wrapper)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    strategy_tree = ast.parse(
        (
            Path(__file__).resolve().parents[1]
            / "strategies/kalshi_optimize.py"
        ).read_text(encoding="utf-8")
    )
    strategy_calls = {
        node.func.id
        for node in ast.walk(strategy_tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }

    assert "open" in call_names
    assert "_fetch_kalshi_markets_diagnostic_authenticated" in call_names
    assert "fetch_kalshi_markets_diagnostic" in strategy_calls


def test_preflight_output_is_exact_bounded_and_value_free(capsys) -> None:
    exit_code = preflight.main(
        [],
        environment=_environment(),
        private_key_loader=_loader,
    )
    captured = capsys.readouterr()
    lines = dict(line.split("=", 1) for line in captured.out.splitlines())

    assert exit_code == 0
    assert captured.err == ""
    assert lines == {
        "AUTH_RUNTIME_CONTEXT": "PASS",
        "REQUIRED_AUTH_FIELD_NAMES": ",".join(REQUIRED_AUTH_FIELD_NAMES),
        "MISSING_AUTH_FIELD_COUNT": "0",
        "AUTH_PARSE_STATUS": "PASS",
        "DIRECT_SECRET_FILE_ACCESS": "no",
        "NETWORK_CALL_PERFORMED": "no",
    }
    assert SYNTHETIC_KEY not in captured.out
    assert SYNTHETIC_PRIVATE_MATERIAL not in captured.out


@pytest.mark.parametrize(
    ("environment", "loader", "expected_context", "expected_exit"),
    [
        ({}, _loader, WARN, 2),
        (_environment(), _invalid_loader, FAIL, 1),
    ],
)
def test_preflight_failure_is_bounded_without_exception_text(
    environment,
    loader,
    expected_context,
    expected_exit,
    capsys,
) -> None:
    assert (
        preflight.main(
            [],
            environment=environment,
            private_key_loader=loader,
        )
        == expected_exit
    )
    captured = capsys.readouterr()

    assert f"AUTH_RUNTIME_CONTEXT={expected_context}" in captured.out
    assert SYNTHETIC_EXCEPTION not in captured.out
    assert SYNTHETIC_EXCEPTION not in captured.err
    assert captured.err == ""


def test_cli_rejects_all_arguments_before_client_use(capsys) -> None:
    client = FakeClient(_result(DiscoveryOutcome.SUCCESS_EMPTY))

    assert cli.main(["--api-key", SYNTHETIC_KEY], client=client) == 2
    captured = capsys.readouterr()
    assert client.calls == 0
    assert captured.out == ""
    assert captured.err == ""


def test_injected_client_exception_is_bounded_and_redacted(capsys) -> None:
    client = FakeClient(error=RuntimeError(SYNTHETIC_EXCEPTION))

    assert cli.main([], client=client) == 1
    captured = capsys.readouterr()
    assert "DISCOVERY_OUTCOME=INTERNAL_DISCOVERY_ERROR" in captured.out
    assert SYNTHETIC_EXCEPTION not in captured.out
    assert SYNTHETIC_EXCEPTION not in captured.err
    assert captured.err == ""


def test_no_synthetic_values_identifiers_or_hashes_are_emitted(capsys) -> None:
    client = FakeClient(_result(DiscoveryOutcome.SUCCESS_NONEMPTY))

    assert cli.main([], client=client) == 0
    output = capsys.readouterr().out
    for forbidden in (
        SYNTHETIC_KEY,
        SYNTHETIC_PRIVATE_MATERIAL,
        SYNTHETIC_RECORD,
        SYNTHETIC_EXCEPTION,
        "ticker",
        "title",
        "event",
        "hash",
        "https://",
    ):
        assert forbidden.lower() not in output.lower()


def test_import_has_no_secret_file_network_or_sqlite_access() -> None:
    code = r'''
import sys

def audit(event, args):
    if event == "open" and args:
        target = str(args[0]).lower()
        forbidden = (".env", ".pem", ".key", ".bash_history", "/keys/", "/secrets/")
        if any(marker in target for marker in forbidden):
            raise RuntimeError("forbidden file access")
    if event.startswith("socket.connect"):
        raise RuntimeError("forbidden network access")

sys.addaudithook(audit)
import scripts.expanded_shadow_discovery_redacted
import scripts.expanded_shadow_discovery_auth_preflight_redacted
assert "sqlite3" not in sys.modules
print("IMPORT_BOUNDARY=PASS")
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
    assert completed.stdout == "IMPORT_BOUNDARY=PASS\n"
    assert completed.stderr == ""


@pytest.mark.parametrize(
    ("source", "expected_rule"),
    [
        (
            'from pathlib import Path\nPath(".env").read_text()\n',
            "ENV_FILE_LITERAL",
        ),
        ('open("synthetic-disposable.pem", "rb")\n', "SECRET_FILE_LITERAL"),
        ("from dotenv import load_dotenv\nload_dotenv()\n", "DOTENV_LOAD"),
        (
            'import argparse\nargparse.ArgumentParser().add_argument("--api-key")\n',
            "CREDENTIAL_CLI_OPTION",
        ),
        ("import os\nprint(dict(os.environ))\n", "RAW_ENVIRONMENT_ACCESS"),
        (
            "import os\nvalues = os.environ\nprint(dict(values))\n",
            "RAW_ENVIRONMENT_ACCESS",
        ),
        (
            'import os\nos.open("synthetic", os.O_RDONLY)\n',
            "FILE_OPEN",
        ),
        (
            'import requests\nresult = requests.get("https://synthetic.invalid")\n',
            "NETWORK_AT_IMPORT",
        ),
        (
            "client = SyntheticAuthenticatedClient()\n",
            "MODULE_LEVEL_CLIENT_CONSTRUCTION",
        ),
        (
            "import sys\noutput = object()\nsys.stdout.write(output)\n",
            "ARBITRARY_OUTPUT",
        ),
    ],
)
def test_static_checker_rejects_unsafe_synthetic_fixtures(
    tmp_path: Path,
    source: str,
    expected_rule: str,
) -> None:
    fixture = tmp_path / "synthetic_unsafe_fixture.py"
    fixture.write_text(source, encoding="utf-8")

    report = checker.check_paths([fixture])

    assert report.status == "FAIL"
    assert expected_rule in report.violation_rules


def test_static_checker_fails_closed_on_parse_error(tmp_path: Path) -> None:
    fixture = tmp_path / "synthetic_parse_error.py"
    fixture.write_text("def broken(:\n", encoding="utf-8")

    report = checker.check_paths([fixture])

    assert report.status == "FAIL"
    assert report.violation_rules == ("AST_PARSE_ERROR",)


def test_static_checker_refuses_secret_bearing_scan_target(tmp_path: Path) -> None:
    fixture = tmp_path / "synthetic-disposable.pem"
    fixture.write_text("not real material", encoding="utf-8")

    report = checker.check_paths([fixture])

    assert report.status == "FAIL"
    assert report.violation_rules == ("FORBIDDEN_SCAN_TARGET",)


def test_static_checker_accepts_repaired_dependency_closure() -> None:
    report = checker.check_default_closure()

    assert report.status == "PASS"
    assert report.violation_rules == ()


def test_runtime_discovery_does_not_connect_to_sqlite(monkeypatch) -> None:
    def forbidden_connect(*_args, **_kwargs):
        raise AssertionError("runtime SQLite access is forbidden")

    monkeypatch.setattr(sqlite3, "connect", forbidden_connect)
    result = InheritedEnvironmentDiscoveryClient(
        environment=_environment(),
        private_key_loader=_loader,
        request_get=lambda *_args, **_kwargs: FakeResponse({"series": []}),
        header_factory=lambda *_args: {},
    ).discover()

    assert result.outcome is DiscoveryOutcome.SUCCESS_EMPTY
