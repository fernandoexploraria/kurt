# Kurt Sandbox Replay — Version 2.0

`kurt_sandbox_v2.py` is a conservative, point-in-time-aware rewrite of the original long-only OHLC replay script. It retains discounted limit entries, ATR-based sizing, trailing stops, the optional loser leash, factor adjustments, local artifacts, and optional Google Sheets publication while correcting material look-ahead, intrabar-order, accounting, and operational-safety defects.

The program is a historical simulator. It does not place brokerage orders.

## Requirements

- Python 3.10 or later
- Local CSV/JSON replay: Python standard library only
- RapidAPI replay: the optional `requests` package
- Tests: `pytest`

Install the optional network dependency with:

```bash
python -m pip install -r requirements_kurt_sandbox_v2.txt
```

## Recommended neutral local replay

```bash
python kurt_sandbox_v2.py \
  --tickers REGN,DIS \
  --start-date 2026-01-01 \
  --days 60 \
  --data-dir ./historical_data \
  --factor-mode off \
  --output-dir ./replay_output
```

Legacy underscore spellings such as `--start_date`, `--data_dir`, and `--factor_mode neutral` are also accepted.

Each ticker file should be named `TICKER.csv` or `TICKER.json`. Supported fields are:

```text
time | date | timestamp
open | o
high | max | h
low | min | l
close | c
volume | v     (optional)
```

## Point-in-time factor replay

Version 2.0 ignores undated factor records in point-in-time mode. Dated records may be embedded in the existing factor JSON files under `snapshots`, `history`, `records`, or date-keyed objects. A separate snapshot directory is also supported.

Layout 1:

```text
snapshots/quiver_shield_2026-01-15.json
snapshots/optimized_entries_2026-01-15.json
snapshots/dea_scores_2026-01-15.json
```

Layout 2:

```text
snapshots/2026-01-15/quiver_shield.json
snapshots/2026-01-15/optimized_entries.json
snapshots/2026-01-15/dea_scores.json
```

Each file is a ticker-keyed JSON object. Run with:

```bash
python kurt_sandbox_v2.py \
  --tickers REGN,DIS \
  --start-date 2026-01-01 \
  --days 60 \
  --data-dir ./historical_data \
  --factor-mode point-in-time \
  --factor-snapshot-dir ./snapshots
```

For every decision, the latest eligible record dated on or before the preceding bar is selected. Future snapshots are not selected.

## Static compatibility mode

```bash
python kurt_sandbox_v2.py ... --factor-mode static
```

This applies current undated Quiver, optimized-entry, and DEA files in the manner closest to the original script. It emits a look-ahead warning and should not be used to claim unbiased historical performance.

## Intrabar ambiguity

Daily and aggregated OHLC bars do not reveal whether their high or low occurred first. The default is conservative:

```bash
--intrabar-policy conservative
```

Accepted alternatives are `optimistic`, `ohlc`, and `olhc`. The aliases `stop_first` and `target_first` are also accepted. Ambiguous exits are identified in the closed-trade ledger and counted in the summary.

## Portfolio controls

The principal controls are:

```text
--risk-per-trade 0.01
--max-position-pct 0.10
--max-portfolio-risk-pct 0.06
--max-positions 10
```

Aggregate open risk is estimated from each position's entry outlay and active stop. By default, exit proceeds generated on an OHLC bar cannot fund another security's entry on that same bar because cross-symbol event ordering is unknown. The less conservative behavior is available only through:

```bash
--reuse-same-bar-exit-cash
```

## Costs

```text
--commission-bps 5
--slippage-bps 0
```

`--cost-per-side-bps` is accepted as an alias for `--commission-bps`. Costs are recorded separately in every transaction and included in realized P&L and liquidation-equivalent equity.

## RapidAPI mode

```bash
export RAPIDAPI_KEY='...'
python kurt_sandbox_v2.py \
  --tickers REGN,DIS \
  --start-date 2026-01-01 \
  --days 60 \
  --factor-mode off
```

The current endpoint is count-based. The script estimates an oversized request, validates the requested date coverage and ATR warmup, retries transient failures, and returns a data error when coverage is insufficient. Use `--range-count` to override the estimate.

## Google Sheets publication

Publication is disabled by default. Local artifacts are written first. To opt in:

```bash
export REPLAY_SHEET_ID='...'
export GOG_ACCOUNT='...'
python kurt_sandbox_v2.py ... --publish-sheets
```

Without `--sheet-required`, a publication failure is logged but does not invalidate a successful local replay. With `--sheet-required`, publication failure returns exit code 4. No account or Sheet identifier is embedded in the source.

## Output files

The output directory receives:

- `replay_summary.json` — canonical summary
- `replay_history.json` — compatibility copy of the summary
- `replay_positions.json`
- `replay_transactions.csv`
- `replay_completed_trades.csv`
- `replay_closed_trades.csv` — compatibility alias
- `replay_equity_curve.csv`
- `runs/<run-id>/...` — immutable per-run artifacts

The equity curve contains cash, gross marked equity, liquidation-equivalent equity, estimated open risk, and position count.

## Exit codes

| Code | Meaning |
|---:|---|
| 0 | Replay completed; optional publication either succeeded or was not required |
| 2 | Configuration error |
| 3 | Market-data error |
| 4 | Required Google Sheets publication failed |
| 5 | Other expected replay failure |
| 10 | Unexpected internal failure |
| 130 | Interrupted by the operator |

## Testing

```bash
pytest -q -W error test_kurt_sandbox_v2.py
```

The delivered test suite covers timestamp parsing, OHLC validation, point-in-time factors, intrabar ambiguity, entry-bar causality, stop ratcheting, position and portfolio risk caps, same-bar cash reuse, trade pairing, missing-bar valuation, timeframe-adjusted Sharpe, CLI compatibility, artifact generation, and an offline end-to-end replay.
