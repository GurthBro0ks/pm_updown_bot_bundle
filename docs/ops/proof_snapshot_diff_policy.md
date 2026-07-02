# Proof Snapshot Diff Policy

Decorative proof headers are not protected content. Do not compare full proof
files directly when the files contain intentional labels such as:

```text
=== CRON HASH BEFORE / SANITIZED SNAPSHOT ===
=== CRON HASH AFTER / SANITIZED SNAPSHOT ===
```

Those rows are useful for humans, but they create false failures when the
machine check is supposed to prove that protected content or hashes did not
change.

## Safe Comparison Rules

1. Prefer machine-only proof files for invariants. A file that contains only
   `sha256sum` output can be compared with `cmp -s`.
2. If before/after proof files contain decorative headers, compare normalized
   rows with `scripts/proof_compare.py`.
3. Do not print raw unified diffs for proof files that may contain secrets,
   tokens, webhooks, private keys, environment variables, or raw runtime
   configuration.
4. Use `--show-diff` only for files that have already been reviewed as
   non-secret.

## Helper Pattern

```bash
./venv/bin/python3 scripts/proof_compare.py \
  "$PROOF_DIR/unrelated_hashes_before.txt" \
  "$PROOF_DIR/unrelated_hashes_after.txt"
```

Default output is limited to result, normalized SHA256 values, and row counts:

```text
PROOF_COMPARE_RESULT=PASS
LEFT_NORMALIZED_SHA256=<sha>
RIGHT_NORMALIZED_SHA256=<sha>
LEFT_ROWS=<n>
RIGHT_ROWS=<n>
```

## Cron Hash Pattern

For cron stability, store only the hash in each file. Do not include raw cron
content unless the active phase explicitly needs a sanitized cron audit.

```bash
crontab -l 2>/dev/null | sha256sum > "$PROOF_DIR/cron_before_sha.txt"

# ...run the bounded validation...

crontab -l 2>/dev/null | sha256sum > "$PROOF_DIR/cron_after_sha.txt"
cmp -s "$PROOF_DIR/cron_before_sha.txt" "$PROOF_DIR/cron_after_sha.txt"
```

If a human-readable header is required for traceability, use the helper:

```bash
{
  echo "=== CRON HASH BEFORE / SANITIZED SNAPSHOT ==="
  crontab -l 2>/dev/null | sha256sum
} > "$PROOF_DIR/cron_before.txt"

{
  echo "=== CRON HASH AFTER / SANITIZED SNAPSHOT ==="
  crontab -l 2>/dev/null | sha256sum
} > "$PROOF_DIR/cron_after.txt"

./venv/bin/python3 scripts/proof_compare.py \
  "$PROOF_DIR/cron_before.txt" \
  "$PROOF_DIR/cron_after.txt"
```

## Unrelated Dirty File Hash Pattern

Use a fixed allowlist and record hashes only. This proves the unrelated dirty
files stayed byte-identical without printing their contents.

```bash
for f in data/shadow_resolution_cache.json scripts/run_hourly_shadow.sh "!"; do
  if [ -e "$f" ]; then
    sha256sum "$f"
  else
    echo "MISSING $f"
  fi
done > "$PROOF_DIR/unrelated_hashes_before.txt"

# ...run the bounded validation...

for f in data/shadow_resolution_cache.json scripts/run_hourly_shadow.sh "!"; do
  if [ -e "$f" ]; then
    sha256sum "$f"
  else
    echo "MISSING $f"
  fi
done > "$PROOF_DIR/unrelated_hashes_after.txt"

./venv/bin/python3 scripts/proof_compare.py \
  "$PROOF_DIR/unrelated_hashes_before.txt" \
  "$PROOF_DIR/unrelated_hashes_after.txt"
```
