# Hourly Shadow Env Policy

`scripts/run_hourly_shadow.sh` must keep Kalshi credential values out of source.
The wrapper loads them from the repo `.env` at runtime and fails closed when the
file is missing or unreadable.

The wrapper validates only the required variable names and never prints values:

- `KALSHI_KEY`
- `KALSHI_SECRET_FILE`, which must point to a readable key file

The wrapper preserves the existing hourly shadow entrypoint:

```bash
python3 scripts/hourly_shadow.py >> logs/hourly.log 2>&1
```

Do not add inline secret assignments to this script. Do not make `.env` loading
optional for the cron wrapper.
