# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run locally
pip install -r requirements.txt
python3 app.py          # starts on http://localhost:5000

# Docker
docker build -t golf-handicap .
docker run -p 5000:5000 -v /path/to/data:/data -e DB_PATH=/data/golf_handicap.db golf-handicap
```

No test suite exists. Verify changes by running the app and exercising the affected flows manually.

## Architecture

Single-file Flask app (`app.py`) with a SQLite backend. Everything — routes, handicap logic, DB schema, and migrations — lives in `app.py`. Templates are in `templates/`, static CSS in `static/style.css`.

**Database** is created and migrated on first request via `ensure_db()` (a `before_request` hook). `migrate_db()` uses `ALTER TABLE` guards (`PRAGMA table_info`) so it's safe to run on existing databases. The DB path defaults to `~/golf_handicap.db` and is overridable via `DB_PATH`. A SQLite trigger (`calc_differential`) fires on `INSERT` into `round` to compute the 18-hole differential automatically; 9-hole rounds override this with a manual `UPDATE` after insert.

**Auth** is two-layered:
- *Admin*: gates course/tee mutations. Enabled only if `ADMIN_PASSWORD` env var is set. Session key `admin`.
- *Per-golfer*: optional password per golfer stored as a Werkzeug scrypt hash. Session key `golfer_<id>`. Golfers without a password pass through without a login prompt.

**Handicap calculation** (`calculate_handicap`) is stateless — it reads the most recent 20 rounds from the DB, applies the WHS sliding scale, exceptional score reductions, and the 0.96 multiplier, and returns `(index, n, used_round_ids)`. Soft/hard cap logic (against Low HI from `handicap_snapshot`) is applied in the calling routes (`dashboard`, `player_report`), not inside `calculate_handicap` itself. Snapshots are written by `save_snapshot` after every round add/edit and deleted when a round is deleted.

**ESC (Equitable Stroke Control)** is applied in `save_hole_scores`: each hole is capped at net double bogey (`par + 2 + strokes_received`). `hole_score.strokes` stores the actual score; `hole_score.adjusted_strokes` stores the ESC-capped value. `round.adjusted_gross_score` uses adjusted strokes for differential calculation; `round.actual_gross_score` preserves the real total.

**9-hole rounds**: differential is calculated using the nine's specific rating/slope (or half the 18-hole values as fallback), then the expected differential for the unplayed nine (`handicap / 2`) is added to produce a full 18-hole equivalent.

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `DB_PATH` | `~/golf_handicap.db` | SQLite file path |
| `SECRET_KEY` | `dev-only-change-in-prod` | Flask session secret — change in production |
| `ADMIN_PASSWORD` | *(unset)* | Enables admin protection when set |
