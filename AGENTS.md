# Slimy Bot Bundle — Agent Operating Manual

You are an autonomous coding agent working in the Polymarket trading bot repo.

## Startup Sequence (do this EVERY session)

1. `pwd` — confirm you're in the repo root
2. `cat claude-progress.md` — understand what happened last session
3. `cat feature_list.json | head -200` — see current feature status
4. `git log --oneline -10` — see recent commits
5. `source init.sh` — validate the environment
6. Pick the highest-priority incomplete feature from feature_list.json
7. Only THEN begin coding

## Repo Structure

- `runner.py` — Main bot runner entry point
- `strategies/` — Trading strategy implementations
- `venues/` — Exchange/venue connectors (Polymarket)
- `utils/` — Shared utilities
- `scripts/` — Run scripts, test scripts, automation
- `docs/` — Documentation and design notes
- `notes/` — Working notes and research
- `logs/` — Runtime logs (gitignored in prod)
- `.ralph/` — Ralph automation configs

## Deeper Docs (read when relevant)

- `docs/` — Strategy docs, architecture notes
- `notes/` — Research and working notes

## Truth Gate (non-negotiable)

A feature is only "done" when:
1. `./scripts/run_tests.sh` exits 0
2. If it's a strategy change: shadow-mode run produces expected output
3. No regressions in existing strategies

## Forbidden Zones (DO NOT TOUCH)

- `.env*` (never read/write secrets)
- `secrets/**`, `infra/**`, `prod/**`
- Any wallet/key/seed/mnemonic material (wherever it lives)
- Generated outputs: `data/**`, `artifacts/**`, `flight_recorder/**`, `tmp/**`, `/tmp/**`

If a task requires touching forbidden paths, STOP and propose an alternative.

## Work Rules

- ONE feature per session. Complete it or document where you stopped.
- Prefer minimal changes. Keep diffs small and surgical.
- No auto-commits. Leave changes uncommitted for human review.
- Add/update tests so the truth gate can prove correctness.
- Produce a buglog entry under `docs/buglog/` when the truth gate passes.

## End-of-Session Checklist

1. `./scripts/run_tests.sh` passes (truth gate)
2. `feature_list.json` updated (passes: true for completed features)
3. `claude-progress.md` updated with what you did, what's next
4. Changes are staged but NOT committed (human reviews first)
5. Buglog entry written if applicable

## Tech Stack Quick Reference

- Language: Python 3.x
- Trading venue: Polymarket (via venues/)
- Strategy pattern: strategies/ directory, each strategy is a module
- Automation: Ralph (.ralph/), shell scripts (scripts/)
- No package manager lock file — use pip/requirements.txt if adding deps
