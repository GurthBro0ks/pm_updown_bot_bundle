#!/usr/bin/env python3
"""Static contract gate for the privileged production-auth discovery bridge."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
import re
import sys
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
BRIDGE_SOURCE = ROOT / "utils/kalshi_discovery_production_bridge.py"
LAUNCHER_SOURCE = ROOT / "scripts/expanded_shadow_discovery_production_auth.py"
PREFLIGHT_SOURCE = (
    ROOT / "scripts/expanded_shadow_discovery_auth_preflight_redacted.py"
)

EXPECTED_PRODUCTION_CLIENT_FACTORY = "KalshiOrderClient"
EXPECTED_OPERATOR_ACTION = "--execute-once"
FORBIDDEN_CALL_LEAVES = frozenset(
    {
        "cancel_order",
        "place_order",
        "get_orders",
        "get_positions",
        "system",
        "popen",
        "Popen",
        "run",
        "check_call",
        "check_output",
        "sleep",
    }
)
FORBIDDEN_SOURCE_TOKENS = (
    "candidate_capture",
    "crontab",
    "cron_",
    "CANDIDATE_LEDGER_SHADOW_ENABLED",
    "EXPANDED_SHADOW_DYNAMIC_ATTRIBUTION",
)
_CREDENTIAL_OPTION = re.compile(
    r"^--.*(?:api[-_]?key|private[-_]?key|secret|token|credential|header)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class BridgeStaticReport:
    status: str
    violation_rules: tuple[str, ...]


def _call_leaf(node: ast.Call) -> str:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return ""


def _parse(path: Path) -> ast.Module:
    if path.is_symlink():
        raise OSError
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def check_bridge_contract() -> BridgeStaticReport:
    violations: set[str] = set()
    try:
        bridge_tree = _parse(BRIDGE_SOURCE)
        launcher_tree = _parse(LAUNCHER_SOURCE)
        preflight_tree = _parse(PREFLIGHT_SOURCE)
    except (OSError, UnicodeError, SyntaxError):
        return BridgeStaticReport("FAIL", ("AST_PARSE_ERROR",))

    bridge_factories = {
        _call_leaf(node)
        for node in ast.walk(bridge_tree)
        if isinstance(node, ast.Call)
        and _call_leaf(node) == EXPECTED_PRODUCTION_CLIENT_FACTORY
    }
    if bridge_factories != {EXPECTED_PRODUCTION_CLIENT_FACTORY}:
        violations.add("PRODUCTION_CLIENT_FACTORY_NOT_EXACT")

    for tree in (bridge_tree, launcher_tree, preflight_tree):
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                leaf = _call_leaf(node)
                if leaf in FORBIDDEN_CALL_LEAVES:
                    violations.add("FORBIDDEN_SIDE_EFFECT_CALL")
                if leaf in {"input", "readline", "read"}:
                    base = node.func.value if isinstance(
                        node.func, ast.Attribute
                    ) else None
                    if leaf == "input" or (
                        isinstance(base, ast.Attribute)
                        and base.attr == "stdin"
                    ):
                        violations.add("CREDENTIAL_STDIN_PATH")
            elif isinstance(node, ast.While):
                violations.add("POLL_OR_RETRY_LOOP")
            elif isinstance(node, ast.Constant) and isinstance(
                node.value, str
            ):
                if _CREDENTIAL_OPTION.match(node.value):
                    violations.add("CREDENTIAL_CLI_OPTION")

    launcher_literals = {
        node.value
        for node in ast.walk(launcher_tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    if EXPECTED_OPERATOR_ACTION not in launcher_literals:
        violations.add("EXPLICIT_ONE_SHOT_FLAG_MISSING")

    combined_source = "\n".join(
        ast.unparse(tree)
        for tree in (bridge_tree, launcher_tree, preflight_tree)
    )
    if any(token in combined_source for token in FORBIDDEN_SOURCE_TOKENS):
        violations.add("CAPTURE_OR_CRON_REFERENCE")

    for tree in (bridge_tree, launcher_tree, preflight_tree):
        for statement in tree.body:
            value = (
                statement.value
                if isinstance(statement, (ast.Assign, ast.Expr))
                else None
            )
            if isinstance(value, ast.Call):
                leaf = _call_leaf(value)
                if leaf.endswith("Client") or leaf in {"get", "request"}:
                    violations.add("MODULE_LEVEL_RUNTIME_ACTION")

    ordered = tuple(sorted(violations))
    return BridgeStaticReport(
        "PASS" if not ordered else "FAIL",
        ordered,
    )


def format_report(report: BridgeStaticReport) -> str:
    rules = ",".join(report.violation_rules) if report.violation_rules else "none"
    lines = (
        f"PRODUCTION_AUTH_BRIDGE_STATIC_CHECK={report.status}",
        f"PRODUCTION_AUTH_BRIDGE_VIOLATION_COUNT={len(report.violation_rules)}",
        f"PRODUCTION_AUTH_BRIDGE_VIOLATION_RULES={rules}",
        "OPERATOR_LAUNCHER_IN_UNPRIVILEGED_CLOSURE=no",
        "PRODUCTION_CLIENT_FACTORY="
        "utils.kalshi_orders.KalshiOrderClient",
        "CREDENTIAL_CLI_ARGUMENTS=no",
        "CREDENTIAL_STDIN_INPUT=no",
        "ORDER_CALLS_ALLOWED=no",
        "CAPTURE_OR_CRON_CALLS_ALLOWED=no",
        "RETRY_OR_POLL_LOOP_ALLOWED=no",
    )
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    if list(sys.argv[1:] if argv is None else argv):
        return 2
    report = check_bridge_contract()
    sys.stdout.write(format_report(report))
    return 0 if report.status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
