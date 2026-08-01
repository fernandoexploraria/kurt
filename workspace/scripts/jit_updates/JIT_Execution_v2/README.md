# JIT Transition-Matrix Regime Diagnostic v2.0

This package replaces the original five-day/full-session proof of concept with a session-aware, matched-window diagnostic designed for controlled pre-trade use.

## What changed

The updated script:

- Never counts overnight or missing-minute observations as one-minute transitions.
- Uses only fully closed bars and validates duplicates, prices, timestamps, exchange sessions, data freshness, and gaps.
- Compares a rolling live window with the same minute-of-session window from prior sessions.
- Removes the silent 80/20 fallback and returns `INSUFFICIENT_DATA` when session history is inadequate.
- Uses robust intraday volatility normalization and adaptive quantile states.
- Shrinks sparse live transition rows toward the historical baseline rather than toward a uniform distribution.
- Reports occupancy, conditional-transition, and joint-transition JSD separately.
- Adds sequence surprise, posterior uncertainty, directional tail metrics, and top transition contributors.
- Calibrates the live reading against approximate leave-one-session-out historical windows.
- Uses order-specific decisions: an anomaly can block new risk but does not automatically block risk reduction.
- Emits stable JSON, explicit exit codes, an analysis ID, timestamps, and a short time-to-live.
- Reads Yahoo Finance for prototype operation, CSV/JSON/Parquet files, and Databento OHLCV DBN/DBN.ZST files.

## Installation

Core and Yahoo Finance prototype source:

```bash
python -m venv jit_env
source jit_env/bin/activate
pip install -r requirements.txt
```

Add local Databento DBN support:

```bash
pip install -r requirements-databento.txt
```

Add Parquet support:

```bash
pip install -r requirements-parquet.txt
```

## Live prototype usage

```bash
python transition_matrix_live.py \
  --ticker MCD \
  --source yfinance \
  --mode live \
  --action new-long \
  --format json \
  --compact-json
```

The default live window is 60 minutes. The script uses the same minute-of-session interval from up to 20 prior sessions, but the Yahoo Finance prototype source will usually provide fewer one-minute sessions than a local Databento history file.

## Historical replay with Databento DBN

The input must contain an OHLCV schema with a `close` field, such as `ohlcv-1m`.

```bash
python transition_matrix_live.py \
  --ticker MCD \
  --source dbn \
  --input MCD.ohlcv-1m.dbn.zst \
  --mode replay \
  --as-of 2026-07-22T15:30:00Z \
  --action diagnostic \
  --format json \
  --output results/MCD_20260722T1530Z.json
```

The official Databento Python client reads local DBN files using `DBNStore.from_file(...).to_df()`. The script uses float price conversion and mapped symbols.

## CSV, JSON Lines, or Parquet replay

Accepted timestamp names include `ts_event`, `timestamp`, `datetime`, `time`, and `date`. Accepted close names include `close`, `Close`, `c`, and `close_px`. A `symbol`, `ticker`, or `raw_symbol` column is used to select the requested ticker when present.

```bash
python transition_matrix_live.py \
  --ticker MCD \
  --source file \
  --input MCD_ohlcv_1m.csv \
  --mode replay \
  --as-of 2026-07-22T15:30:00Z \
  --window-minutes 60 \
  --baseline-sessions 20 \
  --format text
```

For naive timestamps, specify their source timezone:

```bash
--input-timezone America/New_York
```

## Decision contract

| Decision | Exit | Automation meaning |
|---|---:|---|
| `PASS` | 0 | Normal execution controls may continue. |
| `ALLOW_RISK_REDUCTION` | 0 | Warning may exist, but the requested transaction reduces exposure. |
| `CAUTION` | 10 | Do not automatically increase risk; require review or explicit override. |
| `BLOCK_NEW_RISK` | 20 | Block the risk-increasing order unless an audited manual override is supplied. |
| `INSUFFICIENT_DATA` | 30 | No valid decision; fail closed for new risk. |
| `STALE_DATA` | 31 | Latest closed bar is too old; fail closed for new risk. |
| `MARKET_CLOSED` | 32 | The configured exchange is closed at the live as-of time. |
| `DATA_SOURCE_ERROR` | 40 | Input or dependency failure; fail closed for new risk. |
| `INVALID_ARGUMENT` | 41 | Configuration error. |
| `INTERNAL_ERROR` | 50 | Unexpected program failure. |

## Action contexts

Risk-increasing actions:

```text
new-long
add-long
new-short
```

Risk-reducing actions:

```text
sell-profit
stop-loss
reduce-long
cover-short
```

`BLOCK_NEW_RISK` requires all three conditions:

1. Adequate matched-session calibration.
2. An extreme composite anomaly percentile.
3. Directional tail evidence adverse to the proposed risk-increasing order.

A diagnostic run without an order context uses `--action diagnostic` and can return `PASS` or `CAUTION`, but it does not issue an automatic trade block.

## Structured output

Important JSON fields include:

```text
analysis_id
as_of_utc
valid_until_utc
decision
exit_code
data_quality
window
state_model
metrics
calibration
direction
top_transition_contributors
reasons
warnings
```

Execution integration should verify the ticker, action, `analysis_id`, and `valid_until_utc` immediately before submitting an order. An expired or mismatched result is not a valid authorization.

## Tests

```bash
python -m unittest discover -s tests -v
```

The included suite covers JSD properties, session/gap isolation, origin-only occupancy, adaptive state handling, symbol/timestamp validation, market-closed and stale-data statuses, order policy, and end-to-end replay analysis.

## Important limitations

- This is a first-order return-state model, not an order-book model and not a causal detector.
- The local DBN adapter expects one-minute OHLCV. MBO, MBP, trades, spread, and imbalance features are not used by this version.
- The leave-one-session-out calibration keeps the full-baseline state definition fixed. This is intentional for state consistency but is an approximate calibration rather than a fully nested refit.
- A production threshold policy should be validated with walk-forward replay against adverse excursion, slippage, spread, and strategy outcomes.
- Yahoo Finance remains a prototype/research source. Use a controlled feed before unattended execution.

See `JIT_Execution_Upgrade_Report.docx` for the detailed review, rationale, traceability, and deployment recommendations.
