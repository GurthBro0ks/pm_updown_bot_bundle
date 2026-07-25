#!/usr/bin/env python3
"""AST gate for the redacted discovery command's executable dependency closure."""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from pathlib import Path
import re
import sys
from typing import Iterable, Sequence


ROOT = Path(__file__).resolve().parents[1]
KALSHI_SOURCE = ROOT / "utils/kalshi.py"
FULL_CLOSURE_FILES = (
    ROOT / "scripts/expanded_shadow_discovery_redacted.py",
    ROOT / "scripts/expanded_shadow_discovery_auth_preflight_redacted.py",
    ROOT / "utils/kalshi_redacted_discovery.py",
    ROOT / "utils/kalshi_normalize.py",
)
KALSHI_SAFE_ENTRYPOINT = "_fetch_kalshi_markets_diagnostic_authenticated"

ALLOWED_AUTH_FIELD_LITERALS = frozenset(
    {
        "KALSHI_KEY",
        "KALSHI_PRIVATE_KEY_PEM",
    }
)

RULE_NAMES = frozenset(
    {
        "ARBITRARY_OUTPUT",
        "AST_PARSE_ERROR",
        "CREDENTIAL_CLI_OPTION",
        "DOTENV_IMPORT",
        "DOTENV_LOAD",
        "ENV_FILE_LITERAL",
        "EXCEPTION_TEXT_EMISSION",
        "FILE_OPEN",
        "FORBIDDEN_SCAN_TARGET",
        "MODULE_LEVEL_CLIENT_CONSTRUCTION",
        "NETWORK_AT_IMPORT",
        "PRIOR_PROOF_ACCESS",
        "RAW_ENVIRONMENT_ACCESS",
        "SECRET_FILE_LITERAL",
        "SHELL_HISTORY_ACCESS",
        "SHELL_SECRET_SOURCE",
        "SUBPROCESS_SECRET_WRAPPER",
        "UNREVIEWED_AUTH_FIELD_LITERAL",
    }
)

_CREDENTIAL_OPTION = re.compile(
    r"^--.*(?:api[-_]?key|private[-_]?key|secret|token|credential|header|webhook)",
    re.IGNORECASE,
)
_AUTH_FIELD_LITERAL = re.compile(
    r"^[A-Z][A-Z0-9_]*(?:KEY|SECRET|TOKEN|CREDENTIAL|WEBHOOK|AUTH_HEADER)"
    r"(?:_[A-Z0-9_]+)?$"
)
_SECRET_FILE_LITERAL = re.compile(
    r"(?:^|[/\\])(?:[^/\\]*\.(?:pem|key)|keys?[/\\]|credentials?[/\\])",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class StaticCheckReport:
    status: str
    violation_rules: tuple[str, ...]


def _call_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        parts = [node.func.attr]
        value = node.func.value
        while isinstance(value, ast.Attribute):
            parts.append(value.attr)
            value = value.value
        if isinstance(value, ast.Name):
            parts.append(value.id)
        return ".".join(reversed(parts))
    return ""


def _is_os_environ(node: ast.AST, aliases: set[str] | None = None) -> bool:
    if isinstance(node, ast.Name) and aliases and node.id in aliases:
        return True
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "os"
        and node.attr == "environ"
    )


def _is_allowed_output_call(
    node: ast.Call,
    safe_output_names: set[str],
) -> bool:
    name = _call_name(node)
    if name not in {"print", "sys.stdout.write"} or not node.args:
        return False
    argument = node.args[0]
    if isinstance(argument, ast.Name) and argument.id in safe_output_names:
        return True
    return (
        isinstance(argument, ast.Call)
        and _call_name(argument)
        in {"format_discovery_result", "format_auth_runtime_report"}
    )


def _scan_tree(tree: ast.AST) -> set[str]:
    violations: set[str] = set()
    exception_names: set[str] = set()
    environment_aliases: set[str] = set()
    safe_output_names: set[str] = set()
    unsafe_output_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.name:
            exception_names.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = (
                node.targets
                if isinstance(node, ast.Assign)
                else [node.target]
            )
            value = node.value
            target_names = {
                target.id for target in targets if isinstance(target, ast.Name)
            }
            if value is not None and _is_os_environ(value):
                environment_aliases.update(target_names)
            is_safe_formatter = (
                isinstance(value, ast.Call)
                and _call_name(value)
                in {"format_discovery_result", "format_auth_runtime_report"}
            )
            if is_safe_formatter:
                safe_output_names.update(target_names)
            else:
                unsafe_output_names.update(
                    name for name in target_names if name == "output"
                )
    safe_output_names.difference_update(unsafe_output_names)

    if isinstance(tree, ast.Module):
        for statement in tree.body:
            value = None
            if isinstance(statement, (ast.Assign, ast.AnnAssign)):
                value = statement.value
            elif isinstance(statement, ast.Expr):
                value = statement.value
            if not isinstance(value, ast.Call):
                continue
            name = _call_name(value)
            leaf = name.rsplit(".", 1)[-1]
            if leaf.endswith("Client"):
                violations.add("MODULE_LEVEL_CLIENT_CONSTRUCTION")
            if name in {
                "requests.get",
                "requests.post",
                "requests.put",
                "requests.patch",
                "requests.delete",
                "requests.request",
                "socket.create_connection",
                "urllib.request.urlopen",
            }:
                violations.add("NETWORK_AT_IMPORT")

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name == "dotenv" or alias.name.startswith("dotenv.") for alias in node.names):
                violations.add("DOTENV_IMPORT")
        elif isinstance(node, ast.ImportFrom):
            if node.module and (node.module == "dotenv" or node.module.startswith("dotenv.")):
                violations.add("DOTENV_IMPORT")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value
            lowered = value.lower()
            if value in ALLOWED_AUTH_FIELD_LITERALS:
                continue
            if ".env" in lowered:
                violations.add("ENV_FILE_LITERAL")
            if _SECRET_FILE_LITERAL.search(value):
                violations.add("SECRET_FILE_LITERAL")
            if ".bash_history" in lowered or "shell history" in lowered:
                violations.add("SHELL_HISTORY_ACCESS")
            if "/tmp/proof_" in lowered or "proof_pm_" in lowered:
                violations.add("PRIOR_PROOF_ACCESS")
            if _CREDENTIAL_OPTION.match(value):
                violations.add("CREDENTIAL_CLI_OPTION")
            if _AUTH_FIELD_LITERAL.match(value):
                violations.add("UNREVIEWED_AUTH_FIELD_LITERAL")
            if re.search(r"(?:^|[;&|])\s*(?:source|\.)\s+\S+", value):
                violations.add("SHELL_SECRET_SOURCE")
        elif isinstance(node, ast.Call):
            name = _call_name(node)
            if name in {"load_dotenv", "dotenv.load_dotenv", "dotenv_values", "dotenv.dotenv_values"}:
                violations.add("DOTENV_LOAD")
            if name in {
                "open",
                "io.open",
                "os.open",
                "Path.open",
                "pathlib.Path.open",
                "Path.read_text",
                "Path.read_bytes",
                "pathlib.Path.read_text",
                "pathlib.Path.read_bytes",
            } or name.endswith((".open", ".read_text", ".read_bytes")):
                violations.add("FILE_OPEN")
            if name.startswith("subprocess.") or name in {"os.system", "os.popen"}:
                violations.add("SUBPROCESS_SECRET_WRAPPER")
            if name in {"dict", "list", "tuple", "repr", "str"} and any(
                _is_os_environ(argument, environment_aliases)
                for argument in node.args
            ):
                violations.add("RAW_ENVIRONMENT_ACCESS")
            if name in {"print", "sys.stdout.write", "sys.stderr.write"}:
                if not _is_allowed_output_call(node, safe_output_names):
                    violations.add("ARBITRARY_OUTPUT")
            if name in {"str", "repr"} and any(
                isinstance(argument, ast.Name) and argument.id in exception_names
                for argument in node.args
            ):
                violations.add("EXCEPTION_TEXT_EMISSION")
        elif isinstance(node, (ast.For, ast.comprehension)):
            iterator = node.iter
            if _is_os_environ(iterator, environment_aliases):
                violations.add("RAW_ENVIRONMENT_ACCESS")
            if (
                isinstance(iterator, ast.Call)
                and isinstance(iterator.func, ast.Attribute)
                and _is_os_environ(iterator.func.value, environment_aliases)
                and iterator.func.attr in {"items", "keys", "values", "copy"}
            ):
                violations.add("RAW_ENVIRONMENT_ACCESS")
        elif isinstance(node, ast.FormattedValue):
            if isinstance(node.value, ast.Name) and node.value.id in exception_names:
                violations.add("EXCEPTION_TEXT_EMISSION")
    return violations


def _parse_path(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _safe_to_scan(path: Path) -> bool:
    lowered_parts = tuple(part.lower() for part in path.parts)
    name = path.name.lower()
    if name.startswith(".env") or name in {".bash_history", ".zsh_history"}:
        return False
    if path.suffix.lower() in {".pem", ".key"}:
        return False
    return not any(part in {"keys", "credentials", "secrets"} for part in lowered_parts)


def _kalshi_safe_closure(tree: ast.Module) -> ast.Module:
    definitions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    selected = {KALSHI_SAFE_ENTRYPOINT}
    pending = [KALSHI_SAFE_ENTRYPOINT]
    while pending:
        current = pending.pop()
        definition = definitions.get(current)
        if definition is None:
            continue
        referenced = {
            node.id
            for node in ast.walk(definition)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
        }
        for name in sorted(referenced):
            if name in definitions and name not in selected:
                selected.add(name)
                pending.append(name)
    module_level = [
        node
        for node in tree.body
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]
    selected_definitions = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and node.name in selected
    ]
    return ast.Module(body=module_level + selected_definitions, type_ignores=[])


def check_paths(paths: Iterable[Path]) -> StaticCheckReport:
    violations: set[str] = set()
    for path in paths:
        if not _safe_to_scan(path):
            violations.add("FORBIDDEN_SCAN_TARGET")
            continue
        try:
            tree = _parse_path(path)
        except (OSError, UnicodeError, SyntaxError):
            violations.add("AST_PARSE_ERROR")
            continue
        violations.update(_scan_tree(tree))
    ordered = tuple(sorted(violations))
    return StaticCheckReport(
        status="PASS" if not ordered else "FAIL",
        violation_rules=ordered,
    )


def check_default_closure() -> StaticCheckReport:
    report = check_paths(FULL_CLOSURE_FILES)
    violations = set(report.violation_rules)
    try:
        kalshi_tree = _kalshi_safe_closure(_parse_path(KALSHI_SOURCE))
    except (OSError, UnicodeError, SyntaxError):
        violations.add("AST_PARSE_ERROR")
    else:
        violations.update(_scan_tree(kalshi_tree))
    ordered = tuple(sorted(violations))
    return StaticCheckReport(
        status="PASS" if not ordered else "FAIL",
        violation_rules=ordered,
    )


def format_report(report: StaticCheckReport) -> str:
    rules = ",".join(report.violation_rules) if report.violation_rules else "none"
    lines = (
        f"STATIC_SECRET_ACCESS_CHECK={report.status}",
        f"STATIC_SECRET_ACCESS_RULE_COUNT={len(RULE_NAMES)}",
        f"STATIC_SECRET_ACCESS_VIOLATION_COUNT={len(report.violation_rules)}",
        f"STATIC_SECRET_ACCESS_VIOLATION_RULES={rules}",
    )
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--paths", nargs="+", type=Path)
    args = parser.parse_args(argv)
    report = check_paths(args.paths) if args.paths else check_default_closure()
    sys.stdout.write(format_report(report))
    return 0 if report.status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
