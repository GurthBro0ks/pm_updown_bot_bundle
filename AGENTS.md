# Slimy Bot Repo — Agent Rules (Fail-Closed)

You are an autonomous coding agent working in this repository.

## Truth gate (non-negotiable)
- You may only claim success if: `./scripts/run_tests.sh` exits 0.
- Prefer minimal changes. Keep diffs small and surgical.

## Forbidden zones (DO NOT TOUCH)
- .env* (never read/write)
- secrets/**, infra/**, prod/**
- any wallet/key/seed/mnemonic material (wherever it lives)
- generated outputs / flight recorder outputs: data/**, artifacts/**, flight_recorder/**, tmp/**, /tmp/**

If a task would require touching forbidden paths, STOP and propose an alternative approach.

## Workflow
- No auto-commits. Leave changes uncommitted.
- Add/update tests when needed so the truth gate can prove correctness.
- Produce a short report + buglog entry under docs/buglog/ when gate passes.

