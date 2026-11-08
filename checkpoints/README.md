Backfill checkpoint data — written by .github/workflows/backfill.yml and kept
current by .github/workflows/daily_update.yml, not meant to be edited by
hand. `raw/` is the single canonical ~10-year daily OHLCV set per symbol
(merged from the original 5-year backfill plus an older-history extension
on 2026-11-08; see docs/SPEC.md section 4c). There is no second/parallel
checkpoint directory — if one shows up on disk, it's a scratch artifact of
some other script's run, not part of this pipeline.
