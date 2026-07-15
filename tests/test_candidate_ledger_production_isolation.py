from __future__ import annotations

import ast
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_FILES = [
    ROOT / "runner.py",
    ROOT / "scripts/cron_micro_live.sh",
    ROOT / "scripts/cron_weather_trade.sh",
    ROOT / "scripts/run_weather_strategy.py",
]
RESEARCH_FILES = sorted((ROOT / "research/candidate_ledger").glob("*.py"))
CLI_FILES = [
    ROOT / "scripts/candidate_ledger_init.py",
    ROOT / "scripts/candidate_ledger_validate.py",
    ROOT / "scripts/candidate_ledger_summary.py",
    ROOT / "scripts/candidate_replay_evaluate.py",
]


def test_production_runners_do_not_import_or_invoke_ledger():
    for path in PRODUCTION_FILES:
        text = path.read_text().lower()
        assert "candidate_ledger" not in text
        assert "candidate-ledger" not in text
        assert "candidate_ledger_init" not in text
        assert "candidate_ledger.sqlite" not in text


def test_research_package_has_no_production_or_network_imports():
    forbidden_roots = {
        "runner", "strategies", "venues", "requests", "httpx", "aiohttp", "socket", "urllib.request"
    }
    for path in RESEARCH_FILES + CLI_FILES:
        tree = ast.parse(path.read_text(), filename=str(path))
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module)
        assert not {name for name in imports if name in forbidden_roots or name.split(".")[0] in forbidden_roots}


def test_import_has_no_production_side_effects():
    code = (
        "import sys; import research.candidate_ledger; "
        "assert 'runner' not in sys.modules; "
        "assert not any(n.startswith('strategies.') for n in sys.modules); "
        "assert not any(n.startswith('venues.') for n in sys.modules)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        env={"PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == 0, result.stderr


def test_phase_1b_and_self_modification_are_explicitly_excluded():
    docs = (ROOT / "docs/research/candidate-ledger-v1.md").read_text().lower()
    architecture = (ROOT / "docs/research/candidate-ledger-architecture.md").read_text().lower()
    assert "phase 1b" in docs and "requires a new" in docs
    assert "autonomous production self-modification" in docs
    assert "research cannot deploy" in architecture
