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


UNSAFE_INDIRECTION_FIXTURES = (
    (
        "getattr_builtins_open",
        'import builtins\ngetattr(builtins, "open")("synthetic_path")\n',
        "FILE_OPEN",
    ),
    (
        "aliased_builtins_open",
        'import builtins as b\ngetattr(b, "open")("synthetic_path")\n',
        "FILE_OPEN",
    ),
    (
        "assigned_opener",
        'import builtins\nopener = getattr(builtins, "open")\n'
        'opener("synthetic_path")\n',
        "FILE_OPEN",
    ),
    (
        "builtins_mapping_open",
        'opener = getattr(__builtins__, "open")\n'
        'opener("synthetic_path")\n',
        "FILE_OPEN",
    ),
    (
        "getattr_os_environ",
        'import os\nvalues = getattr(os, "environ")\n',
        "RAW_ENVIRONMENT_ACCESS",
    ),
    (
        "aliased_os_environ",
        'import os as operating_system\n'
        'values = getattr(operating_system, "environ")\n',
        "RAW_ENVIRONMENT_ACCESS",
    ),
    (
        "assigned_environment",
        'import os\nvalues = getattr(os, "environ")\n'
        'assigned = values\nassigned.get("SYNTHETIC_FIELD")\n',
        "RAW_ENVIRONMENT_ACCESS",
    ),
    (
        "environment_get",
        'import os\nvalues = getattr(os, "environ")\n'
        'values.get("SYNTHETIC_FIELD")\n',
        "RAW_ENVIRONMENT_ACCESS",
    ),
    (
        "environment_subscript",
        'import os\nvalues = getattr(os, "environ")\n'
        'value = values["SYNTHETIC_FIELD"]\n',
        "RAW_ENVIRONMENT_ACCESS",
    ),
    (
        "environment_iteration",
        'import os\nvalues = getattr(os, "environ")\n'
        "for name in values:\n    pass\n",
        "RAW_ENVIRONMENT_ACCESS",
    ),
    (
        "environment_items",
        'import os\nvalues = getattr(os, "environ")\n'
        "for name, value in values.items():\n    pass\n",
        "RAW_ENVIRONMENT_ACCESS",
    ),
    (
        "environment_dict_dump",
        'import os\nvalues = getattr(os, "environ")\ncopy = dict(values)\n',
        "RAW_ENVIRONMENT_ACCESS",
    ),
    (
        "getattr_os_getenv",
        'import os\ngetattr(os, "getenv")("SYNTHETIC_FIELD")\n',
        "RAW_ENVIRONMENT_ACCESS",
    ),
    (
        "assigned_getenv",
        'import os\nlookup = getattr(os, "getenv")\n'
        'lookup("SYNTHETIC_FIELD")\n',
        "RAW_ENVIRONMENT_ACCESS",
    ),
    (
        "path_read_text",
        'from pathlib import Path\npath_obj = Path("synthetic_path")\n'
        'getattr(path_obj, "read_text")()\n',
        "FILE_OPEN",
    ),
    (
        "path_read_bytes",
        'import pathlib\npath_obj = pathlib.Path("synthetic_path")\n'
        'getattr(path_obj, "read_bytes")()\n',
        "FILE_OPEN",
    ),
    (
        "path_open",
        'import pathlib\npath_obj = pathlib.Path("synthetic_path")\n'
        'getattr(path_obj, "open")()\n',
        "FILE_OPEN",
    ),
    (
        "subprocess_run",
        'import subprocess\ngetattr(subprocess, "run")(["synthetic"])\n',
        "SUBPROCESS_SECRET_WRAPPER",
    ),
    (
        "subprocess_popen",
        'import subprocess\ngetattr(subprocess, "Popen")(["synthetic"])\n',
        "SUBPROCESS_SECRET_WRAPPER",
    ),
    (
        "assigned_subprocess",
        'import subprocess as process\n'
        'runner = getattr(process, "run")\nrunner(["synthetic"])\n',
        "SUBPROCESS_SECRET_WRAPPER",
    ),
    (
        "indirect_print_exception",
        'import builtins\nemit = getattr(builtins, "print")\n'
        "try:\n    pass\nexcept Exception as error:\n    emit(error)\n",
        "EXCEPTION_TEXT_EMISSION",
    ),
    (
        "indirect_print_environment",
        'import builtins\nimport os\nemit = getattr(builtins, "print")\n'
        'values = getattr(os, "environ")\nemit(values)\n',
        "ARBITRARY_OUTPUT",
    ),
    (
        "dynamic_security_getattr",
        "import os\nattribute_name = object()\n"
        "target = getattr(os, attribute_name)\n",
        "UNRESOLVED_SECURITY_GETATTR",
    ),
    (
        "chained_getattr",
        'import pathlib\npath_type = getattr(pathlib, "Path")\n'
        'getattr(path_type("synthetic_path"), "read_text")()\n',
        "FILE_OPEN",
    ),
    (
        "renamed_import_assignment_chain",
        'import builtins as renamed\nfirst = renamed\nsecond = first\n'
        'opener = getattr(second, "open")\nopener("synthetic_path")\n',
        "FILE_OPEN",
    ),
    (
        "parse_error",
        "def broken(:\n",
        "AST_PARSE_ERROR",
    ),
    (
        "direct_open_regression",
        'open("synthetic_path")\n',
        "FILE_OPEN",
    ),
    (
        "credential_cli_stdin_regression",
        "import argparse\nimport sys\n"
        'argparse.ArgumentParser().add_argument("--api-key")\n'
        "credential_input = sys.stdin.read()\n",
        "CREDENTIAL_CLI_OPTION",
    ),
    (
        "dotenv_key_shell_regression",
        "from dotenv import load_dotenv\nimport subprocess\n"
        'load_dotenv("synthetic.env")\n'
        'subprocess.run(["sh", "-c", "source synthetic_credentials"])\n',
        "DOTENV_LOAD",
    ),
    (
        "direct_environment_dump_regression",
        "import os\ncopy = dict(os.environ)\n",
        "RAW_ENVIRONMENT_ACCESS",
    ),
    (
        "from_import_open_alias",
        "from builtins import open as file_open\n"
        'file_open("synthetic_path")\n',
        "FILE_OPEN",
    ),
    (
        "from_import_environ_alias",
        "from os import environ as inherited_environment\n"
        "copy = dict(inherited_environment)\n",
        "RAW_ENVIRONMENT_ACCESS",
    ),
    (
        "computed_dynamic_attribute",
        'import builtins\nattribute_name = "op" + "en"\n'
        "target = getattr(builtins, attribute_name)\n",
        "UNRESOLVED_SECURITY_GETATTR",
    ),
    (
        "variable_attribute_name",
        'import builtins\nattribute_name = "open"\n'
        "target = getattr(builtins, attribute_name)\n",
        "UNRESOLVED_SECURITY_GETATTR",
    ),
    (
        "unknown_import_dynamic_attribute",
        "import synthetic_module\nattribute_name = object()\n"
        "target = getattr(synthetic_module, attribute_name)\n",
        "UNRESOLVED_SECURITY_GETATTR",
    ),
    (
        "unknown_wrapper_dynamic_attribute",
        "def make_wrapper():\n    return object()\n"
        "wrapped = make_wrapper()\nattribute_name = object()\n"
        "target = getattr(wrapped, attribute_name)\n",
        "UNRESOLVED_SECURITY_GETATTR",
    ),
    (
        "failed_alias_dynamic_attribute",
        "alias = unresolved_name\nattribute_name = object()\n"
        "target = getattr(alias, attribute_name)\n",
        "UNRESOLVED_SECURITY_GETATTR",
    ),
    (
        "assigned_getattr_builtin",
        "import builtins\nresolve = getattr\n"
        'opener = resolve(builtins, "open")\n'
        'opener("synthetic_path")\n',
        "FILE_OPEN",
    ),
    (
        "indirect_logger_output",
        "import logging\nlogger = logging.getLogger(__name__)\n"
        'emit = getattr(logger, "info")\n'
        "response = object()\nemit(response)\n",
        "ARBITRARY_OUTPUT",
    ),
    (
        "dynamic_import_target",
        "import importlib\n"
        'importer = getattr(importlib, "import_module")\n'
        'importer("synthetic_module")\n',
        "DYNAMIC_IMPORT_ACCESS",
    ),
    (
        "builtins_mapping_subscript_open",
        'opener = __builtins__["open"]\n'
        'opener("synthetic_path")\n',
        "FILE_OPEN",
    ),
    (
        "direct_environment_get_unreviewed_field",
        'import os\nvalue = os.environ.get("SYNTHETIC_FIELD")\n',
        "RAW_ENVIRONMENT_ACCESS",
    ),
    (
        "annotated_target_assignment",
        "import builtins\n"
        'opener: object = getattr(builtins, "open")\n'
        'opener("synthetic_path")\n',
        "FILE_OPEN",
    ),
    (
        "unsupported_dynamic_base_form",
        "attribute_name = object()\n"
        "target = getattr((lambda: object())(), attribute_name)\n",
        "UNRESOLVED_SECURITY_GETATTR",
    ),
)


@pytest.mark.parametrize(
    ("fixture_name", "source", "expected_rule"),
    UNSAFE_INDIRECTION_FIXTURES,
    ids=[case[0] for case in UNSAFE_INDIRECTION_FIXTURES],
)
def test_static_checker_unsafe_indirection_matrix(
    tmp_path: Path,
    fixture_name: str,
    source: str,
    expected_rule: str,
) -> None:
    fixture = tmp_path / f"{fixture_name}.py"
    fixture.write_text(source, encoding="utf-8")

    report = checker.check_paths([fixture])

    assert report.status == "FAIL"
    assert expected_rule in report.violation_rules
    assert set(report.violation_rules) <= checker.RULE_NAMES


SAFE_STATIC_FIXTURES = (
    (
        "required_field_constants",
        'AUTH_KEY_FIELD = "KALSHI_KEY"\n'
        'AUTH_MATERIAL_FIELD = "KALSHI_PRIVATE_KEY_PEM"\n',
    ),
    (
        "exact_presence_checks",
        'import os\nAUTH_KEY_FIELD = "KALSHI_KEY"\n'
        'AUTH_MATERIAL_FIELD = "KALSHI_PRIVATE_KEY_PEM"\n'
        "key_present = AUTH_KEY_FIELD in os.environ\n"
        "material_present = AUTH_MATERIAL_FIELD in os.environ\n",
    ),
    (
        "approved_bounded_output",
        "def format_discovery_result(value):\n"
        '    return f"STATUS={bool(value)}\\n"\n'
        "print(format_discovery_result(True))\n",
    ),
    (
        "local_dataclass_constant_getattr",
        "from dataclasses import dataclass\n"
        "@dataclass\nclass LocalStatus:\n    enabled: bool\n"
        "status = LocalStatus(enabled=True)\n"
        'value = getattr(status, "enabled")\n',
    ),
    (
        "local_object_dynamic_getattr_default",
        "class LocalStatus:\n    pass\n"
        "status = LocalStatus()\nattribute_name = object()\n"
        "value = getattr(status, attribute_name, False)\n",
    ),
    (
        "dependency_injected_fake_client",
        "class FakeClient:\n"
        "    def discover(self):\n        return True\n"
        "def run(client):\n    return client.discover()\n",
    ),
    (
        "auth_preflight_formatter",
        "def format_auth_runtime_report(value):\n"
        '    return f"AUTH_STATUS={bool(value)}\\n"\n'
        "output = format_auth_runtime_report(True)\nprint(output)\n",
    ),
    (
        "discovery_taxonomy",
        "from enum import Enum\n"
        "class DiscoveryOutcome(Enum):\n"
        '    SUCCESS_EMPTY = "SUCCESS_EMPTY"\n'
        '    AUTH_REJECTED = "AUTH_REJECTED"\n',
    ),
    (
        "fake_in_memory_environment",
        'fake_environment = {"KALSHI_KEY": "synthetic", '
        '"KALSHI_PRIVATE_KEY_PEM": "synthetic"}\n'
        'key_present = fake_environment.get("KALSHI_KEY") is not None\n',
    ),
    (
        "reviewed_non_secret_environment_lookup",
        "import os\n"
        'base_url = os.environ.get("KALSHI_BASE_URL", "synthetic")\n',
    ),
)


@pytest.mark.parametrize(
    ("fixture_name", "source"),
    SAFE_STATIC_FIXTURES,
    ids=[case[0] for case in SAFE_STATIC_FIXTURES],
)
def test_static_checker_safe_fixture_matrix(
    tmp_path: Path,
    fixture_name: str,
    source: str,
) -> None:
    fixture = tmp_path / f"{fixture_name}.py"
    fixture.write_text(source, encoding="utf-8")

    report = checker.check_paths([fixture])

    assert report.status == "PASS"
    assert report.violation_rules == ()


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


def test_static_checker_refuses_symlink_scan_target(tmp_path: Path) -> None:
    source = tmp_path / "synthetic_source.py"
    source.write_text("value = True\n", encoding="utf-8")
    link = tmp_path / "synthetic_link.py"
    link.symlink_to(source)

    report = checker.check_paths([link])

    assert report.status == "FAIL"
    assert report.violation_rules == ("FORBIDDEN_SCAN_TARGET",)


def test_static_checker_accepts_repaired_dependency_closure() -> None:
    report = checker.check_default_closure()

    assert report.status == "PASS"
    assert report.violation_rules == ()


def test_static_checker_report_is_rule_only_and_bounded(tmp_path: Path) -> None:
    fixture = tmp_path / "synthetic_output_contract_fixture.py"
    synthetic_literal = "SYNTHETIC_LITERAL_MUST_NOT_BE_EMITTED"
    fixture.write_text(
        "import builtins\n"
        'getattr(builtins, "open")('
        f'"{synthetic_literal}")\n',
        encoding="utf-8",
    )

    report = checker.check_paths([fixture])
    output = checker.format_report(report)

    assert output.splitlines() == [
        "STATIC_SECRET_ACCESS_CHECK=FAIL",
        f"STATIC_SECRET_ACCESS_RULE_COUNT={len(checker.RULE_NAMES)}",
        "STATIC_SECRET_ACCESS_VIOLATION_COUNT=1",
        "STATIC_SECRET_ACCESS_VIOLATION_RULES=FILE_OPEN",
    ]
    assert synthetic_literal not in output
    assert fixture.name not in output


def test_static_checker_does_not_execute_inspected_source(
    tmp_path: Path,
) -> None:
    fixture = tmp_path / "synthetic_never_execute.py"
    fixture.write_text(
        'raise AssertionError("SYNTHETIC_SOURCE_WAS_EXECUTED")\n',
        encoding="utf-8",
    )

    report = checker.check_paths([fixture])

    assert report.status == "PASS"
    assert report.violation_rules == ()


def test_static_checker_reads_only_the_inspected_source_file(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture = tmp_path / "synthetic_source_only.py"
    fixture.write_text(
        "from pathlib import Path\n"
        'Path("synthetic_runtime_path").read_text()\n',
        encoding="utf-8",
    )
    original_read_text = Path.read_text
    read_paths: list[Path] = []

    def guarded_read_text(path: Path, *args, **kwargs):
        read_paths.append(path)
        if path != fixture:
            raise AssertionError("referenced runtime path was opened")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded_read_text)

    report = checker.check_paths([fixture])

    assert report.status == "FAIL"
    assert "FILE_OPEN" in report.violation_rules
    assert read_paths == [fixture]


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
