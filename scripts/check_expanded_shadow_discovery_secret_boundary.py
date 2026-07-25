#!/usr/bin/env python3
"""Bounded AST gate for the redacted discovery executable dependency closure.

This resolves reviewed imports, simple assignments, and literal-string
``getattr`` targets. It is intentionally not a general Python interpreter or
universal metaprogramming proof.
"""

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
    ROOT / "utils/kalshi_redacted_discovery.py",
    ROOT / "utils/kalshi_normalize.py",
)
KALSHI_SAFE_ENTRYPOINT = "_fetch_kalshi_markets_diagnostic_with_auth_headers"

ALLOWED_AUTH_FIELD_LITERALS = frozenset()
ALLOWED_NON_SECRET_ENVIRONMENT_LOOKUP_LITERALS = frozenset(
    {
        "KALSHI_BASE_URL",
        "KALSHI_FETCH_INCLUDE_CATEGORIES",
        "KALSHI_FETCH_MIN_LIQUIDITY_USD",
        "KALSHI_SERIES_LIMIT",
    }
)

RULE_NAMES = frozenset(
    {
        "ARBITRARY_OUTPUT",
        "AST_PARSE_ERROR",
        "CREDENTIAL_CLI_OPTION",
        "DYNAMIC_IMPORT_ACCESS",
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
        "UNRESOLVED_SECURITY_GETATTR",
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


_BUILTINS_MODULE = "BUILTINS_MODULE"
_OS_MODULE = "OS_MODULE"
_PATHLIB_MODULE = "PATHLIB_MODULE"
_SUBPROCESS_MODULE = "SUBPROCESS_MODULE"
_SYS_MODULE = "SYS_MODULE"
_IO_MODULE = "IO_MODULE"
_IMPORTLIB_MODULE = "IMPORTLIB_MODULE"
_DOTENV_MODULE = "DOTENV_MODULE"
_NETWORK_MODULE = "NETWORK_MODULE"
_LOGGING_MODULE = "LOGGING_MODULE"
_UNKNOWN_IMPORTED_MODULE = "UNKNOWN_IMPORTED_MODULE"
_BUILTIN_GETATTR = "BUILTIN_GETATTR"
_FILE_CALL = "FILE_CALL"
_PATH_CONSTRUCTOR = "PATH_CONSTRUCTOR"
_PATH_OBJECT = "PATH_OBJECT"
_ENVIRONMENT = "ENVIRONMENT"
_INDIRECT_ENVIRONMENT = "INDIRECT_ENVIRONMENT"
_ENVIRONMENT_LOOKUP = "ENVIRONMENT_LOOKUP"
_INDIRECT_ENVIRONMENT_LOOKUP = "INDIRECT_ENVIRONMENT_LOOKUP"
_ENVIRONMENT_VALUE = "ENVIRONMENT_VALUE"
_ENVIRONMENT_ITERATION = "ENVIRONMENT_ITERATION"
_SUBPROCESS_CALL = "SUBPROCESS_CALL"
_OUTPUT_CALL = "OUTPUT_CALL"
_OUTPUT_STREAM = "OUTPUT_STREAM"
_LOGGER_FACTORY = "LOGGER_FACTORY"
_LOGGER = "LOGGER"
_DYNAMIC_IMPORT_CALL = "DYNAMIC_IMPORT_CALL"
_DOTENV_CALL = "DOTENV_CALL"
_NETWORK_CALL = "NETWORK_CALL"
_UNKNOWN_DYNAMIC_TARGET = "UNKNOWN_DYNAMIC_TARGET"
_UNKNOWN_OBJECT = "UNKNOWN_OBJECT"
_UNKNOWN_WRAPPER_RESULT = "UNKNOWN_WRAPPER_RESULT"
_LOCAL_CLASS = "LOCAL_CLASS"
_SAFE_LOCAL_OBJECT = "SAFE_LOCAL_OBJECT"
_SAFE_OUTPUT = "SAFE_OUTPUT"
_EXCEPTION_VALUE = "EXCEPTION_VALUE"
_EXCEPTION_TEXT = "EXCEPTION_TEXT"

_SAFE_FORMATTERS = frozenset(
    {"format_discovery_result", "format_auth_runtime_report"}
)
_SUBPROCESS_ATTRIBUTES = frozenset(
    {
        "Popen",
        "call",
        "check_call",
        "check_output",
        "getoutput",
        "getstatusoutput",
        "run",
    }
)
_OUTPUT_ATTRIBUTES = frozenset(
    {
        "critical",
        "debug",
        "error",
        "exception",
        "info",
        "log",
        "warning",
        "write",
        "writelines",
    }
)
_ENVIRONMENT_ITERATION_ATTRIBUTES = frozenset(
    {"copy", "items", "keys", "values"}
)
_SECURITY_RELEVANT_BASE_LABELS = frozenset(
    {
        _BUILTINS_MODULE,
        _OS_MODULE,
        _PATHLIB_MODULE,
        _SUBPROCESS_MODULE,
        _SYS_MODULE,
        _IO_MODULE,
        _IMPORTLIB_MODULE,
        _DOTENV_MODULE,
        _NETWORK_MODULE,
        _LOGGING_MODULE,
        _UNKNOWN_IMPORTED_MODULE,
        _FILE_CALL,
        _PATH_OBJECT,
        _ENVIRONMENT,
        _INDIRECT_ENVIRONMENT,
        _ENVIRONMENT_LOOKUP,
        _INDIRECT_ENVIRONMENT_LOOKUP,
        _SUBPROCESS_CALL,
        _OUTPUT_CALL,
        _OUTPUT_STREAM,
        _LOGGER,
        _DYNAMIC_IMPORT_CALL,
        _DOTENV_CALL,
        _NETWORK_CALL,
        _UNKNOWN_OBJECT,
        _UNKNOWN_WRAPPER_RESULT,
    }
)


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


def _module_labels(module: str) -> set[str]:
    root = module.split(".", 1)[0]
    if root == "builtins":
        return {_BUILTINS_MODULE}
    if root == "os":
        return {_OS_MODULE}
    if root == "pathlib":
        return {_PATHLIB_MODULE}
    if root == "subprocess":
        return {_SUBPROCESS_MODULE}
    if root == "sys":
        return {_SYS_MODULE}
    if root == "io":
        return {_IO_MODULE}
    if root == "importlib":
        return {_IMPORTLIB_MODULE}
    if root == "dotenv":
        return {_DOTENV_MODULE}
    if root in {"requests", "socket", "urllib", "http"}:
        return {_NETWORK_MODULE}
    if root == "logging":
        return {_LOGGING_MODULE}
    return {_UNKNOWN_IMPORTED_MODULE}


class _SemanticResolver:
    """Bounded target labels for imports and simple assignments.

    This deliberately does not evaluate source or attempt general Python data
    flow. Labels are conservative unions within one parsed file.
    """

    def __init__(self, tree: ast.AST) -> None:
        self.name_labels: dict[str, set[str]] = {}
        self.local_classes = {
            node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)
        }
        self.constant_strings: dict[str, set[str]] = {}
        self.assignments: list[tuple[tuple[str, ...], ast.AST]] = []
        self._collect_declarations(tree)
        self._resolve_assignments()

    def _add_labels(self, name: str, labels: set[str]) -> bool:
        if not labels:
            return False
        known = self.name_labels.setdefault(name, set())
        before = len(known)
        known.update(labels)
        return len(known) != before

    def _collect_declarations(self, tree: ast.AST) -> None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    bound = alias.asname or alias.name.split(".", 1)[0]
                    module = alias.name if alias.asname else bound
                    self._add_labels(bound, _module_labels(module))
            elif isinstance(node, ast.ImportFrom):
                module_labels = _module_labels(node.module or "")
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    bound = alias.asname or alias.name
                    labels = self.attribute_labels(
                        module_labels,
                        alias.name,
                        via_getattr=False,
                    )
                    self._add_labels(
                        bound,
                        labels or {_UNKNOWN_IMPORTED_MODULE},
                    )
            elif isinstance(node, ast.ClassDef):
                self._add_labels(node.name, {_LOCAL_CLASS})
            elif isinstance(node, ast.ExceptHandler) and node.name:
                self._add_labels(node.name, {_EXCEPTION_VALUE})
            elif isinstance(node, ast.Assign):
                names = tuple(
                    target.id
                    for target in node.targets
                    if isinstance(target, ast.Name)
                )
                if names:
                    self.assignments.append((names, node.value))
                    if isinstance(node.value, ast.Constant) and isinstance(
                        node.value.value, str
                    ):
                        for name in names:
                            self.constant_strings.setdefault(name, set()).add(
                                node.value.value
                            )
            elif isinstance(node, ast.AnnAssign) and isinstance(
                node.target, ast.Name
            ):
                if node.value is not None:
                    self.assignments.append(((node.target.id,), node.value))
                    if isinstance(node.value, ast.Constant) and isinstance(
                        node.value.value, str
                    ):
                        self.constant_strings.setdefault(
                            node.target.id, set()
                        ).add(node.value.value)

    def _resolve_assignments(self) -> None:
        for _iteration in range(len(self.assignments) + 1):
            changed = False
            for names, value in self.assignments:
                labels = self.labels(value)
                if not labels:
                    if isinstance(value, ast.Call):
                        labels = {_UNKNOWN_WRAPPER_RESULT}
                    elif isinstance(value, ast.Name):
                        labels = {_UNKNOWN_OBJECT}
                    elif isinstance(
                        value,
                        (ast.Dict, ast.List, ast.Set, ast.Tuple),
                    ):
                        labels = {_SAFE_LOCAL_OBJECT}
                for name in names:
                    changed = self._add_labels(name, labels) or changed
            if not changed:
                break

    def _constant_attribute(self, node: ast.AST) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        return None

    def _dynamic_getattr_is_relevant(
        self,
        base: ast.AST,
        base_labels: set[str],
    ) -> bool:
        if base_labels & _SECURITY_RELEVANT_BASE_LABELS:
            return True
        if _SAFE_LOCAL_OBJECT in base_labels:
            return False
        if isinstance(
            base,
            (ast.Constant, ast.Dict, ast.List, ast.Set, ast.Tuple),
        ):
            return False
        return True

    def _suspicious_unknown_attribute(self, attribute: str) -> set[str]:
        if attribute in {"open", "read_bytes", "read_text"}:
            return {_FILE_CALL}
        if attribute == "environ":
            return {_INDIRECT_ENVIRONMENT}
        if attribute == "getenv":
            return {_INDIRECT_ENVIRONMENT_LOOKUP}
        if attribute in _SUBPROCESS_ATTRIBUTES or attribute in {
            "popen",
            "system",
        }:
            return {_SUBPROCESS_CALL}
        if attribute in {"print"} or attribute in _OUTPUT_ATTRIBUTES:
            return {_OUTPUT_CALL}
        if attribute in {"__import__", "import_module"}:
            return {_DYNAMIC_IMPORT_CALL}
        if attribute in {"load_dotenv", "dotenv_values"}:
            return {_DOTENV_CALL}
        return set()

    def attribute_labels(
        self,
        base_labels: set[str],
        attribute: str,
        *,
        via_getattr: bool,
    ) -> set[str]:
        labels: set[str] = set()
        for base_label in base_labels:
            if base_label == _BUILTINS_MODULE:
                if attribute in {"open"}:
                    labels.add(_FILE_CALL)
                elif attribute == "print":
                    labels.add(_OUTPUT_CALL)
                elif attribute == "getattr":
                    labels.add(_BUILTIN_GETATTR)
                elif attribute == "__import__":
                    labels.add(_DYNAMIC_IMPORT_CALL)
            elif base_label == _OS_MODULE:
                if attribute == "environ":
                    labels.add(
                        _INDIRECT_ENVIRONMENT
                        if via_getattr
                        else _ENVIRONMENT
                    )
                elif attribute == "getenv":
                    labels.add(
                        _INDIRECT_ENVIRONMENT_LOOKUP
                        if via_getattr
                        else _ENVIRONMENT_LOOKUP
                    )
                elif attribute == "open":
                    labels.add(_FILE_CALL)
                elif attribute in {"popen", "system"}:
                    labels.add(_SUBPROCESS_CALL)
            elif base_label == _PATHLIB_MODULE and attribute == "Path":
                labels.add(_PATH_CONSTRUCTOR)
            elif base_label == _PATH_OBJECT and attribute in {
                "open",
                "read_bytes",
                "read_text",
            }:
                labels.add(_FILE_CALL)
            elif (
                base_label == _SUBPROCESS_MODULE
                and attribute in _SUBPROCESS_ATTRIBUTES
            ):
                labels.add(_SUBPROCESS_CALL)
            elif base_label == _SYS_MODULE and attribute in {
                "stdout",
                "stderr",
            }:
                labels.add(_OUTPUT_STREAM)
            elif base_label == _OUTPUT_STREAM and attribute in {
                "write",
                "writelines",
            }:
                labels.add(_OUTPUT_CALL)
            elif base_label == _IO_MODULE and attribute == "open":
                labels.add(_FILE_CALL)
            elif (
                base_label == _IMPORTLIB_MODULE
                and attribute == "import_module"
            ):
                labels.add(_DYNAMIC_IMPORT_CALL)
            elif base_label == _DOTENV_MODULE and attribute in {
                "load_dotenv",
                "dotenv_values",
            }:
                labels.add(_DOTENV_CALL)
            elif base_label == _NETWORK_MODULE and attribute in {
                "connect",
                "create_connection",
                "delete",
                "get",
                "open",
                "patch",
                "post",
                "put",
                "request",
                "urlopen",
            }:
                labels.add(_NETWORK_CALL)
            elif base_label == _LOGGING_MODULE:
                if attribute == "getLogger":
                    labels.add(_LOGGER_FACTORY)
                elif attribute in _OUTPUT_ATTRIBUTES:
                    labels.add(_OUTPUT_CALL)
            elif base_label == _LOGGER and attribute in _OUTPUT_ATTRIBUTES:
                labels.add(_OUTPUT_CALL)
            elif base_label in {_ENVIRONMENT, _INDIRECT_ENVIRONMENT}:
                if attribute == "get":
                    labels.add(
                        _INDIRECT_ENVIRONMENT_LOOKUP
                        if base_label == _INDIRECT_ENVIRONMENT
                        else _ENVIRONMENT_LOOKUP
                    )
                elif attribute in _ENVIRONMENT_ITERATION_ATTRIBUTES:
                    labels.add(_ENVIRONMENT_ITERATION)
            elif base_label in {
                _UNKNOWN_IMPORTED_MODULE,
                _UNKNOWN_OBJECT,
                _UNKNOWN_WRAPPER_RESULT,
            }:
                labels.update(
                    self._suspicious_unknown_attribute(attribute)
                )
        return labels

    def _getattr_labels(self, node: ast.Call) -> set[str]:
        if len(node.args) < 2:
            return {_UNKNOWN_DYNAMIC_TARGET}
        base = node.args[0]
        base_labels = self.labels(base)
        attribute = self._constant_attribute(node.args[1])
        if attribute is None:
            if self._dynamic_getattr_is_relevant(base, base_labels):
                return {_UNKNOWN_DYNAMIC_TARGET}
            return set()
        labels = self.attribute_labels(
            base_labels,
            attribute,
            via_getattr=True,
        )
        if labels:
            return labels
        if not base_labels or base_labels & {
            _UNKNOWN_IMPORTED_MODULE,
            _UNKNOWN_OBJECT,
            _UNKNOWN_WRAPPER_RESULT,
        }:
            return self._suspicious_unknown_attribute(attribute)
        return set()

    def labels(self, node: ast.AST | None) -> set[str]:
        if node is None:
            return set()
        if isinstance(node, ast.Name):
            direct = {
                "getattr": {_BUILTIN_GETATTR},
                "open": {_FILE_CALL},
                "print": {_OUTPUT_CALL},
                "__builtins__": {_BUILTINS_MODULE},
                "__import__": {_DYNAMIC_IMPORT_CALL},
                "object": {_LOCAL_CLASS},
            }.get(node.id, set())
            return set(self.name_labels.get(node.id, set())) | set(direct)
        if isinstance(node, ast.Attribute):
            return self.attribute_labels(
                self.labels(node.value),
                node.attr,
                via_getattr=False,
            )
        if isinstance(node, ast.Subscript):
            base_labels = self.labels(node.value)
            if _BUILTINS_MODULE in base_labels:
                attribute = self._constant_attribute(node.slice)
                if attribute is not None:
                    return self.attribute_labels(
                        {_BUILTINS_MODULE},
                        attribute,
                        via_getattr=True,
                    )
            if base_labels & {_ENVIRONMENT, _INDIRECT_ENVIRONMENT}:
                return {_ENVIRONMENT_VALUE}
            return set()
        if isinstance(node, ast.IfExp):
            return self.labels(node.body) | self.labels(node.orelse)
        if isinstance(node, ast.BoolOp):
            labels: set[str] = set()
            for value in node.values:
                labels.update(self.labels(value))
            return labels
        if isinstance(node, ast.JoinedStr):
            labels: set[str] = set()
            for value in node.values:
                if isinstance(value, ast.FormattedValue):
                    labels.update(self.labels(value.value))
            return labels
        if isinstance(node, ast.Call):
            function_labels = self.labels(node.func)
            if _BUILTIN_GETATTR in function_labels:
                return self._getattr_labels(node)
            if _PATH_CONSTRUCTOR in function_labels:
                return {_PATH_OBJECT}
            if _LOGGER_FACTORY in function_labels:
                return {_LOGGER}
            if function_labels & {
                _ENVIRONMENT_LOOKUP,
                _INDIRECT_ENVIRONMENT_LOOKUP,
            }:
                return {_ENVIRONMENT_VALUE}
            if _ENVIRONMENT_ITERATION in function_labels:
                return {
                    _INDIRECT_ENVIRONMENT
                    if _INDIRECT_ENVIRONMENT_LOOKUP in function_labels
                    else _ENVIRONMENT
                }
            if _DYNAMIC_IMPORT_CALL in function_labels:
                return {_UNKNOWN_IMPORTED_MODULE}
            if _EXCEPTION_VALUE in function_labels:
                return {_EXCEPTION_TEXT}
            if isinstance(node.func, ast.Name) and node.func.id in _SAFE_FORMATTERS:
                return {_SAFE_OUTPUT}
            if _LOCAL_CLASS in function_labels:
                return {_SAFE_LOCAL_OBJECT}
            if function_labels & {
                _UNKNOWN_IMPORTED_MODULE,
                _UNKNOWN_OBJECT,
                _UNKNOWN_WRAPPER_RESULT,
            }:
                return {_UNKNOWN_WRAPPER_RESULT}
            return set()
        if isinstance(node, ast.FormattedValue):
            return self.labels(node.value)
        if isinstance(node, (ast.Dict, ast.List, ast.Set, ast.Tuple)):
            return {_SAFE_LOCAL_OBJECT}
        return set()


def _is_allowed_output_call(
    node: ast.Call,
    resolver: _SemanticResolver,
    safe_output_names: set[str],
) -> bool:
    if not node.args:
        return False
    argument = node.args[0]
    if isinstance(argument, ast.Name) and argument.id in safe_output_names:
        return True
    return _SAFE_OUTPUT in resolver.labels(argument)


def _is_allowed_environment_presence_check(
    node: ast.Compare,
    resolver: _SemanticResolver,
) -> bool:
    if len(node.ops) != 1 or not isinstance(node.ops[0], (ast.In, ast.NotIn)):
        return False
    if len(node.comparators) != 1:
        return False
    if not resolver.labels(node.comparators[0]) & {
        _ENVIRONMENT,
        _INDIRECT_ENVIRONMENT,
    }:
        return False
    if isinstance(node.left, ast.Constant) and isinstance(node.left.value, str):
        return node.left.value in ALLOWED_AUTH_FIELD_LITERALS
    if isinstance(node.left, ast.Name):
        return resolver.constant_strings.get(node.left.id, set()) <= (
            ALLOWED_AUTH_FIELD_LITERALS
        ) and bool(resolver.constant_strings.get(node.left.id))
    return False


def _is_allowed_direct_environment_lookup(
    node: ast.Call,
    resolver: _SemanticResolver,
) -> bool:
    if not node.args:
        return False
    field = node.args[0]
    if isinstance(field, ast.Constant) and isinstance(field.value, str):
        return field.value in ALLOWED_NON_SECRET_ENVIRONMENT_LOOKUP_LITERALS
    if isinstance(field, ast.Name):
        values = resolver.constant_strings.get(field.id, set())
        return bool(values) and values <= (
            ALLOWED_NON_SECRET_ENVIRONMENT_LOOKUP_LITERALS
        )
    return False


def _scan_tree(tree: ast.AST) -> set[str]:
    violations: set[str] = set()
    resolver = _SemanticResolver(tree)
    safe_output_names: set[str] = set()
    unsafe_output_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = (
                node.targets
                if isinstance(node, ast.Assign)
                else [node.target]
            )
            value = node.value
            target_names = {
                target.id for target in targets if isinstance(target, ast.Name)
            }
            is_safe_formatter = (
                isinstance(value, ast.Call)
                and _SAFE_OUTPUT in resolver.labels(value)
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
            function_labels = resolver.labels(value.func)
            if _NETWORK_CALL in function_labels or name in {
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
            function_labels = resolver.labels(node.func)
            result_labels = resolver.labels(node)
            if _UNKNOWN_DYNAMIC_TARGET in result_labels:
                violations.add("UNRESOLVED_SECURITY_GETATTR")
            if _DYNAMIC_IMPORT_CALL in function_labels:
                violations.add("DYNAMIC_IMPORT_ACCESS")
            if _DOTENV_CALL in function_labels or name in {
                "load_dotenv",
                "dotenv.load_dotenv",
                "dotenv_values",
                "dotenv.dotenv_values",
            }:
                violations.add("DOTENV_LOAD")
            if _FILE_CALL in function_labels or name in {
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
            if (
                _SUBPROCESS_CALL in function_labels
                or name.startswith("subprocess.")
                or name in {"os.system", "os.popen"}
            ):
                violations.add("SUBPROCESS_SECRET_WRAPPER")
            if _INDIRECT_ENVIRONMENT in result_labels:
                violations.add("RAW_ENVIRONMENT_ACCESS")
            if _INDIRECT_ENVIRONMENT_LOOKUP in function_labels:
                violations.add("RAW_ENVIRONMENT_ACCESS")
            if (
                _ENVIRONMENT_LOOKUP in function_labels
                and not _is_allowed_direct_environment_lookup(node, resolver)
            ):
                violations.add("RAW_ENVIRONMENT_ACCESS")
            if _ENVIRONMENT_ITERATION in function_labels:
                violations.add("RAW_ENVIRONMENT_ACCESS")
            if name in {
                "bool",
                "dict",
                "iter",
                "len",
                "list",
                "repr",
                "set",
                "sorted",
                "str",
                "tuple",
            } and any(
                resolver.labels(argument)
                & {_ENVIRONMENT, _INDIRECT_ENVIRONMENT}
                for argument in node.args
            ):
                violations.add("RAW_ENVIRONMENT_ACCESS")
            if _OUTPUT_CALL in function_labels or name in {
                "print",
                "sys.stdout.write",
                "sys.stderr.write",
            }:
                if not _is_allowed_output_call(
                    node,
                    resolver,
                    safe_output_names,
                ):
                    violations.add("ARBITRARY_OUTPUT")
                if any(
                    resolver.labels(argument)
                    & {_EXCEPTION_VALUE, _EXCEPTION_TEXT}
                    for argument in node.args
                ):
                    violations.add("EXCEPTION_TEXT_EMISSION")
            if any(
                resolver.labels(argument)
                & {
                    _ENVIRONMENT,
                    _INDIRECT_ENVIRONMENT,
                    _ENVIRONMENT_VALUE,
                }
                for argument in node.args
            ) and (
                _OUTPUT_CALL in function_labels
                or name
                in {
                    "print",
                    "sys.stdout.write",
                    "sys.stderr.write",
                }
            ):
                violations.add("RAW_ENVIRONMENT_ACCESS")
            if name in {"str", "repr"} and any(
                _EXCEPTION_VALUE in resolver.labels(argument)
                for argument in node.args
            ):
                violations.add("EXCEPTION_TEXT_EMISSION")
        elif isinstance(node, (ast.For, ast.comprehension)):
            iterator = node.iter
            if resolver.labels(iterator) & {
                _ENVIRONMENT,
                _INDIRECT_ENVIRONMENT,
            }:
                violations.add("RAW_ENVIRONMENT_ACCESS")
            if (
                isinstance(iterator, ast.Call)
                and _ENVIRONMENT_ITERATION in resolver.labels(iterator.func)
            ):
                violations.add("RAW_ENVIRONMENT_ACCESS")
        elif isinstance(node, ast.Subscript):
            if resolver.labels(node.value) & {
                _ENVIRONMENT,
                _INDIRECT_ENVIRONMENT,
            }:
                violations.add("RAW_ENVIRONMENT_ACCESS")
        elif isinstance(node, ast.Compare):
            if (
                resolver.labels(node.comparators[0])
                & {_ENVIRONMENT, _INDIRECT_ENVIRONMENT}
                if node.comparators
                else False
            ) and not _is_allowed_environment_presence_check(node, resolver):
                violations.add("RAW_ENVIRONMENT_ACCESS")
        elif isinstance(node, ast.FormattedValue):
            if _EXCEPTION_VALUE in resolver.labels(node.value):
                violations.add("EXCEPTION_TEXT_EMISSION")
    return violations


def _parse_path(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _safe_to_scan(path: Path) -> bool:
    if path.is_symlink():
        return False
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
