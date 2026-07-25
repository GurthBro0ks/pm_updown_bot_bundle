#!/usr/bin/env python3
"""Backward-compatible no-network production-auth preflight entry point."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Callable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.expanded_shadow_discovery_production_auth import (
    main as production_auth_main,
)


def main(
    argv: Sequence[str] | None = None,
    *,
    environment: Mapping[str, str] | None = None,
    client_factory: Callable[[], object] | None = None,
    path_exists: Callable[[str], bool] | None = None,
    stat_func: Callable | None = None,
    parse_checker: Callable[[str], bool] | None = None,
    effective_uid: int | None = None,
) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args:
        return 2
    kwargs = {
        "environment": environment,
        "path_exists": path_exists,
        "stat_func": stat_func,
        "parse_checker": parse_checker,
        "effective_uid": effective_uid,
    }
    if client_factory is not None:
        kwargs["client_factory"] = client_factory
    return production_auth_main([], **kwargs)


if __name__ == "__main__":
    raise SystemExit(main())
