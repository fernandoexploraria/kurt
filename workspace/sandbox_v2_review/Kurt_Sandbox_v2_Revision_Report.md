# Kurt Sandbox Replay Version 2.0

## Code Review, Revisions, and Rationale

**Prepared:** July 23, 2026  
**Original:** `kurt_sandbox (1) (1).py`  
**Revised:** `kurt_sandbox_v2.py`  
**Validation status:** 18 automated tests passed; local standard-library-only smoke replay passed

---

## 1. Executive summary

The original program had a coherent strategy concept: buy pullbacks with limit orders, size positions from ATR-based risk, protect positions with a loser leash or trailing stop, take profit at a calculated target, and publish the replay results. However, several implementation details could make the reported backtest materially more favorable or less reliable than a strategy that could actually have been traded.

The most important defects were:

- current factor files could be applied to historical decisions, creating look-ahead bias;
- a missing price could be replaced by the final price in the entire dataset, directly leaking future information into valuation and sizing;
- daily OHLC bars were treated as though the sequence of the high and low were known;
- a stop could be recalculated from information in a bar and then implicitly applied to that same bar;
- repeated trades in the same ticker could be matched to the wrong buy when win rate was calculated;
- the nominal 10% position cap could be bypassed by the one-share fallback;
- exit proceeds from one security could fund another security on the same OHLC bar even though cross-symbol event order was unknown;
- Google Sheets was modified automatically under hard-coded identifiers;
- string timestamps, old date ranges, missing bars, and intraday Sharpe annualization were not handled reliably.

Version 2.0 retains the broad strategy but rebuilds the replay engine around four principles:

1. **Causality:** every decision uses only information available before or at the simulated event.
2. **Conservatism:** unresolved OHLC ordering defaults against the position rather than in its favor.
3. **Auditability:** every round trip has a unique ID, explicit costs, stored entry and exit plans, and reproducible artifacts.
4. **Operational safety:** local replay is the default; cloud publication is explicit, configurable, and verified.

The revised engine is substantially more credible as a research backtester. It is not proof that the trading strategy has positive expectancy, and it remains an OHLC-bar simulator rather than a tick-accurate execution model.

---

## 2. Review objectives and boundaries

The revision was intended to:

- preserve the original long-only pullback-entry concept;
- remove direct and indirect future-data leakage;
- make intrabar assumptions explicit;
- correct trade accounting and performance labels;
- prevent position and portfolio risk constraints from being bypassed;
- support deterministic local historical replay;
- make point-in-time auxiliary data practical;
- prevent an ordinary replay from modifying cloud documents;
- improve error reporting, testing, and maintainability;
- retain useful compatibility with the original command-line spelling and output consumers.

The revision deliberately does **not**:

- place brokerage orders;
- claim institutional-flow or order-book inference from OHLC data;
- manufacture a precise event sequence that the bar data does not contain;
- establish that the strategy is profitable;
- solve survivorship bias, corporate-action normalization, or market-impact modeling without suitable source data.

---

## 3. Material correctness defects and their revisions

### 3.1 Current factor files applied to historical decisions

**Original behavior:** Quiver, optimized-entry, and DEA JSON files were loaded once and their current values were used throughout the historical replay. Unless those files were themselves point-in-time archives, a score calculated after the simulated date could affect a historical entry, target, or size.

**Revision:** Version 2.0 has three explicit factor modes:

| Mode | Behavior | Point-in-time interpretation |
|---|---|---|
| `off` / `neutral` | No Quiver, optimized-entry, or DEA adjustment | Safest neutral baseline |
| `point-in-time` / `snapshots` | Latest dated record on or before the preceding bar | Suitable when snapshot provenance is trustworthy |
| `static` | Current undated records are applied | Compatibility mode; warning emitted |

Undated records are ignored in point-in-time mode. Dated records can be embedded in the existing JSON files or loaded from `--factor-snapshot-dir`. The loader supports both:

```text
snapshots/quiver_shield_2026-01-15.json
snapshots/optimized_entries_2026-01-15.json
snapshots/dea_scores_2026-01-15.json
```

and:

```text
snapshots/2026-01-15/quiver_shield.json
snapshots/2026-01-15/optimized_entries.json
snapshots/2026-01-15/dea_scores.json
```

**Rationale:** A backtest is valid only when every input was available at the simulated decision time. Merely storing a current score in a file does not make it historical.

### 3.2 Future terminal price used when a bar was missing

**Original behavior:** If a held ticker had no candle for the current replay timestamp, valuation fell back to the last candle in the entire downloaded dataset. That candle could be days or weeks in the future.

**Revision:** The engine maintains a last-known-price map that is advanced only after each completed replay bar. A missing bar is valued using the most recent price known at that simulated time. No terminal-dataset fallback remains.

**Rationale:** Future-price leakage can affect the equity curve, position sizing, drawdown, and every later allocation decision. It is one of the most serious backtest defects.

### 3.3 Unknown high/low order treated optimistically

**Original behavior:** When a daily bar touched both the stop and take-profit target, the target was processed first. This assumes the profitable event occurred first even though OHLC data does not contain that sequence.

**Revision:** `--intrabar-policy` makes the assumption explicit:

| Policy | Ambiguous existing position | Non-opening entry-bar treatment |
|---|---|---|
| `conservative` / `stop_first` | Stop wins | A same-bar stop can occur; an unearned target is not awarded |
| `optimistic` / `target_first` | Target wins | Target may win |
| `ohlc` | Open → high → low → close path | High before low is modeled |
| `olhc` | Open → low → high → close path | Low before high is modeled |

The default is `conservative`. Ambiguous exits are marked in the closed-trade ledger, counted in the summary, and disclosed in warnings.

**Rationale:** There is no universally correct sequence for an aggregated bar. The assumption must be visible and sensitivity-tested rather than hidden in `if/elif` ordering.

### 3.4 Current-bar information used to create a current-bar stop

**Original behavior:** The bar's high and current ATR were used to update the trailing stop before the same bar's low was tested. The script could therefore construct a stop from information that occurred later in the bar and then apply it retroactively.

**Revision:** The replay is phased:

1. Evaluate stops and targets that were active before the bar.
2. Evaluate and execute new entries.
3. Resolve any causally supportable entry-bar event.
4. Update high-water marks and stops after the bar closes.
5. Activate the revised stop on the next bar.

**Rationale:** A stop derived from a completed bar cannot protect an earlier part of that bar unless lower-resolution event data proves the sequence.

### 3.5 Entry-bar high credited before the entry could have occurred

**Original behavior:** A new position's high-water mark was initialized with the full bar high. If the high occurred before the limit order filled on the low, the trade received an unearned trailing-stop benefit.

**Revision:** The initial high-water mark is the fill price. For a non-opening fill, the completed bar high is credited only when the selected intrabar path makes it observable after entry. Under the conservative default, the engine uses the entry price or closing price rather than automatically awarding the full high.

**Rationale:** Pre-entry price movement cannot improve the management of a position that did not yet exist.

### 3.6 Stop could loosen as ATR expanded

**Original behavior:** The stop was recalculated as `highest price − current ATR × multiplier`. A rising ATR could move the stop downward.

**Revision:** The position stores the active stop, and each update uses:

```text
new stop = max(existing stop, newly calculated stop)
```

The stop can ratchet upward or remain unchanged; it cannot loosen.

**Rationale:** A trailing stop should not quietly increase risk after entry unless that behavior is a separately defined strategy rule.

### 3.7 Repeated sells matched to the first buy in the ticker

**Original behavior:** Win rate searched for the first buy transaction with the same ticker. A later round trip in that security could therefore be compared with an unrelated earlier entry.

**Revision:** Every position receives a unique `trade_id`. The buy, sell, and completed-trade row carry that same ID. Realized P&L is calculated at the exit from the exact position object rather than reconstructed by ticker search.

**Rationale:** Ticker is not a trade identifier. Exact lot pairing is required for win rate, expectancy, holding time, and auditability.

### 3.8 Position cap bypassed by the one-share fallback

**Original behavior:** After a 10% capital cap reduced the calculated size to zero, the script could force a one-share purchase. A single expensive share could exceed the cap.

**Revision:** The fallback was removed. Size is the minimum allowed by:

- per-trade risk budget;
- remaining portfolio-risk budget;
- maximum position value;
- available cash;
- maximum position count.

If those constraints permit zero shares, the signal is skipped.

**Rationale:** A risk limit is not a limit if an exception silently overrides it.

### 3.9 Inclusive end-date ambiguity

**Original behavior:** The end timestamp was calculated by adding `days`, and both endpoints were included. A one-day request could include two date labels.

**Revision:** The replay interval is explicitly:

```text
start <= timestamp < end_exclusive
```

Both `end_exclusive` and a compatibility `end_date` are written to the summary.

**Rationale:** Half-open intervals are unambiguous and compose cleanly across consecutive replay windows.

### 3.10 Broken string timestamp parsing

**Original behavior:** A string timestamp was split into a list and passed to `datetime.strptime`, causing the row to be discarded. Numeric timestamps happened to work.

**Revision:** The standard-library parser supports:

- date-only ISO strings;
- ISO timestamps with `Z` or offsets;
- common slash-separated dates;
- epoch seconds, milliseconds, microseconds, and nanoseconds;
- naive timestamps normalized to UTC.

Malformed rows are counted and disclosed.

**Rationale:** Silent loss of all string-dated bars can invalidate the replay while appearing to be a data-availability problem.

### 3.11 Count-based retrieval did not guarantee requested coverage

**Original behavior:** The API request asked for `days + 30` bars but did not anchor the request to the requested start date. Older windows or intraday timeframes could be absent.

**Revision:** Version 2.0 estimates a larger range from the timeframe, requested duration, and ATR warmup. It then validates:

- that at least one bar lies in the requested interval;
- that the start date is actually covered;
- that at least the configured ATR warmup exists before the first replay bar.

A clear data error is returned when coverage is insufficient. `--range-count` can override the estimate, and local archive mode is recommended for reproducible research.

**Rationale:** A bar count is not equivalent to a date range. Validation is essential when the provider interface cannot request exact boundaries.

### 3.12 Intraday Sharpe annualization was incorrect

**Original behavior:** Every equity observation was treated as daily and annualized with `sqrt(252)`, including hourly or minute bars.

**Revision:** Periods per year are derived from the timeframe:

- daily: 252;
- weekly: 52;
- hourly: approximately 252 × 6.5;
- intraday minutes: approximately 252 × 390 / bar minutes.

The annual risk-free rate is converted to a per-period rate before excess returns are calculated.

**Rationale:** Risk-adjusted performance is not meaningful when the annualization factor does not match the sampling frequency.

### 3.13 Automatic modification of a hard-coded Google Sheet

**Original behavior:** Every successful replay attempted to create tabs, clear ranges, overwrite transactions, and append results under a hard-coded account and Sheet ID.

**Revision:** No personal account or Sheet identifier remains in the source. Publication is disabled by default and requires:

```text
--publish-sheets
--sheet-id or REPLAY_SHEET_ID
--gog-account or GOG_ACCOUNT
```

Local artifacts are persisted before publication is attempted. Every `gog` command is executed with `shell=False`, a timeout, and checked return status. Without `--sheet-required`, a publication error is logged but the successful local replay remains successful. With `--sheet-required`, the process returns exit code 4.

**Rationale:** A sandbox replay should be locally safe by default. External side effects must be explicit and verifiable.

---

## 4. Strategy and portfolio-risk improvements

### 4.1 Portfolio-wide open-risk cap

The original per-trade risk calculation did not constrain aggregate risk across simultaneous positions. Version 2.0 adds:

```text
--max-portfolio-risk-pct 0.06
```

For each open position, estimated stop risk is:

```text
entry outlay − estimated net proceeds at the active stop
```

A new position is sized against only the remaining portfolio-risk budget.

**Rationale:** Ten individually reasonable trades can still produce an unreasonable portfolio if their risks are aggregated without a cap.

### 4.2 Maximum concurrent positions

```text
--max-positions 10
```

limits the number of open holdings.

**Rationale:** A large watchlist should not create an uncontrolled number of small positions merely because each one independently passes sizing rules.

### 4.3 Deterministic simultaneous-signal allocation

The original result depended on ticker iteration order. Version 2.0 gathers all candidates at a timestamp, ranks them deterministically using the existing DEA, catalyst, and regime multipliers, and uses ticker as the final tie-breaker.

**Rationale:** Competing signals must produce repeatable results. The ranking is explicit; it is not claimed to be optimal.

### 4.4 Same-bar exit cash is not reused by default

Across different securities, an OHLC bar does not reveal whether one security's exit happened before another security's entry. Version 2.0 therefore freezes the entry cash budget at bar-open cash. Same-bar exit proceeds do not fund another entry unless:

```text
--reuse-same-bar-exit-cash
```

is explicitly supplied.

**Rationale:** Reusing proceeds assumes a cross-symbol event sequence that aggregated bars do not provide.

### 4.5 Fixed take-profit plan per position

The original target was recalculated on every bar from current ATR and current factor files. Version 2.0 calculates the target at entry using preceding-bar information and stores it in the position.

**Rationale:** A persisted trade plan is causal, reproducible, and auditable. Dynamic target management can be added later as an explicit rule rather than occurring indirectly through changing input files.

### 4.6 Factor adjustment applies to trade distance

The original modifier multiplied the full stock price. A small percentage applied to a high-priced stock could shift an entry or target far more than intended.

Version 2.0 applies the bounded adjustment to:

- the discount distance below the preceding close; and
- the target distance above entry.

**Rationale:** The factor should alter the strategy's intended distance, not the security's entire nominal price.

### 4.7 Explicit transaction friction

Version 2.0 exposes:

```text
--commission-bps 5
--slippage-bps 0
```

`--cost-per-side-bps` remains an accepted alias. Fees and slippage are separate fields on every transaction. They affect cash, realized P&L, open-position liquidation value, and total return.

**Rationale:** Separate cost components are easier to calibrate, audit, and sensitivity-test than one unexplained price adjustment.

---

## 5. Data-quality and software-engineering improvements

### 5.1 Deterministic local replay mode

`--data-dir` loads exact CSV or JSON files without an API. Supported fields include:

```text
time | date | timestamp
open | o
high | max | h
low | min | l
close | c
volume | v (optional)
```

**Rationale:** Local archived data is reproducible, testable, and avoids paying for or downloading the same history repeatedly.

### 5.2 Standard-library local path

Pandas and NumPy are no longer required. CSV parsing, CSV writing, timestamp parsing, and Sharpe calculations use the Python standard library. `requests` is imported only inside RapidAPI mode.

The local replay was executed successfully with `python -S`, which disables normal site-package loading.

**Rationale:** A smaller dependency surface simplifies deployment and makes offline replay more robust.

### 5.3 OHLC and timestamp validation

The loader checks:

- positive, finite OHLC values;
- high not below open, low, or close;
- low not above open, high, or close;
- nonnegative finite volume when supplied;
- duplicate timestamps;
- minimum bar count;
- ATR warmup;
- requested-range coverage.

Invalid rows are discarded and counted; duplicate timestamps are replaced deterministically and counted.

### 5.4 Network error handling

RapidAPI requests use:

- proper URL encoding;
- HTTP status checking;
- JSON validation;
- a finite timeout;
- configurable retries with exponential backoff;
- a concise final data error.

### 5.5 Atomic and versioned artifacts

JSON and CSV outputs are written through temporary files, flushed, synchronized, permissioned, and atomically replaced. The latest named artifacts are accompanied by immutable per-run files under:

```text
runs/<run-id>/
```

**Rationale:** A crash should not leave a half-written state file, and separate runs should remain auditable.

### 5.6 Narrower exception handling and stable exit codes

| Exit code | Meaning |
|---:|---|
| 0 | Replay completed; optional publication succeeded or was not required |
| 2 | Configuration error |
| 3 | Market-data error |
| 4 | Required publication failure |
| 5 | Other expected replay failure |
| 10 | Unexpected internal failure |
| 130 | Operator interruption |

Broad silent exception blocks were removed from the main data and execution paths.

### 5.7 Backward-compatible command-line spellings

The parser accepts canonical hyphenated options and common legacy underscore spellings, including:

```text
--start-date / --start_date
--data-dir / --data_dir
--factor-mode / --factor_mode
--publish-sheets / --publish_sheet
```

It also accepts `neutral` as an alias for factor mode `off`, `snapshots` for `point-in-time`, `stop_first` for the conservative intrabar policy, and `target_first` for the optimistic policy.

**Rationale:** Correctness improvements should not impose unnecessary migration friction on existing automation.

---

## 6. Accounting and reporting corrections

### 6.1 Exact fee-aware round-trip accounting

For a buy:

```text
entry outlay = shares × fill price + entry fee + entry slippage
```

For a sell:

```text
net proceeds = shares × fill price − exit fee − exit slippage
realized P&L = net proceeds − entry outlay
```

Both sides are stored with the same `trade_id`.

### 6.2 Correct performance labels

The revised summary distinguishes:

- final cash;
- final gross marked equity;
- final liquidation-equivalent equity after estimated sell costs;
- realized P&L;
- open unrealized P&L;
- transaction legs;
- completed round trips;
- open positions.

The misleading phrase “Total Realized Return” was removed because total equity may include open unrealized gains or losses.

### 6.3 Expanded metrics

Version 2.0 reports:

- total liquidation-equivalent return;
- win rate and profit factor when defined;
- average win, average loss, and expectancy;
- maximum peak-to-trough drawdown;
- timeframe-adjusted Sharpe ratio;
- total fees and slippage;
- candidate and skipped entry signals;
- skipped signals caused by position or portfolio-risk limits;
- ambiguous exit-bar count;
- maximum estimated open risk observed.

### 6.4 Output artifacts

| File | Purpose |
|---|---|
| `replay_summary.json` | Canonical configuration, results, coverage, warnings, and metrics |
| `replay_history.json` | Compatibility copy of the summary |
| `replay_positions.json` | Detailed final open-position state |
| `replay_transactions.csv` | All buy and sell cash-flow legs |
| `replay_completed_trades.csv` | One row per completed round trip |
| `replay_closed_trades.csv` | Compatibility alias of completed trades |
| `replay_equity_curve.csv` | Cash, gross equity, liquidation equity, open risk, and position count |
| `runs/<run-id>/...` | Immutable artifacts for a specific replay |

Compatibility fields such as `date`, `value`, `trades_count`, and `end_date` are retained alongside more precise Version 2 fields.

---

## 7. Important behavioral changes

These changes are intentional and may reduce reported performance relative to the original.

| Behavior | Original | Version 2.0 default |
|---|---|---|
| Auxiliary factors | Current files always applied | Only dated eligible records; undated records ignored |
| Ambiguous stop and target | Target won | Stop wins |
| Current-bar stop update | Could affect same bar | Active next bar |
| Entry-bar high | Full high credited | Only causally observable high credited |
| Entry-bar stop | Could be ignored | Processed conservatively |
| Stop ratchet | Could loosen | Never loosens |
| Missing price | Final dataset price | Last price known at simulated time |
| One-share fallback | Could break position cap | Removed |
| Aggregate risk | No portfolio cap | Configurable portfolio-risk cap |
| Same-bar exit cash | Reused | Not reused without opt-in |
| End date | Effectively inclusive | Explicitly exclusive |
| Google Sheets | Automatically attempted | Opt-in only |
| Sheet/account | Hard-coded | External configuration |
| Total return label | “Realized” | Liquidation-equivalent total return |

A lower return after these changes is not evidence that Version 2.0 is worse. In many cases it is the expected consequence of removing optimistic or noncausal assumptions.

---

## 8. Usage examples

### 8.1 Recommended neutral local replay

```bash
python kurt_sandbox_v2.py \
  --tickers REGN,DIS \
  --start-date 2026-01-01 \
  --days 60 \
  --data-dir ./historical_data \
  --factor-mode off \
  --output-dir ./replay_output
```

### 8.2 Point-in-time snapshot replay

```bash
python kurt_sandbox_v2.py \
  --tickers REGN,DIS \
  --start-date 2026-01-01 \
  --days 60 \
  --data-dir ./historical_data \
  --factor-mode point-in-time \
  --factor-snapshot-dir ./factor_snapshots
```

### 8.3 Static compatibility replay

```bash
python kurt_sandbox_v2.py \
  --tickers REGN,DIS \
  --start-date 2026-01-01 \
  --days 60 \
  --data-dir ./historical_data \
  --factor-mode static
```

This intentionally permits current undated factors and emits a look-ahead warning.

### 8.4 RapidAPI replay

```bash
export RAPIDAPI_KEY='...'
python kurt_sandbox_v2.py \
  --tickers REGN,DIS \
  --start-date 2026-01-01 \
  --days 60 \
  --factor-mode off
```

### 8.5 Explicit Google Sheets publication

```bash
export REPLAY_SHEET_ID='...'
export GOG_ACCOUNT='...'
python kurt_sandbox_v2.py \
  --tickers REGN,DIS \
  --start-date 2026-01-01 \
  --days 60 \
  --data-dir ./historical_data \
  --factor-mode off \
  --publish-sheets \
  --sheet-required
```

---

## 9. Verification performed

### 9.1 Automated tests

**18 tests passed with warnings treated as errors.** Coverage includes:

- ISO and epoch timestamp parsing;
- invalid-row rejection and duplicate-timestamp handling;
- conservative and optimistic ambiguous-bar resolution;
- entry-bar causality;
- non-loosening trailing stops;
- enforcement of the position-value cap;
- enforcement of the portfolio-risk budget;
- point-in-time record selection and exclusion of future records;
- rejection of undated records outside static mode;
- both documented snapshot-directory layouts;
- timeframe-adjusted Sharpe annualization;
- exact `trade_id` pairing and fee-aware P&L;
- legacy boolean, underscore, and policy aliases;
- exclusive end-date filtering;
- last-known-price valuation when a bar is missing;
- default prohibition and explicit opt-in for same-bar exit-cash reuse;
- complete local artifact generation;
- offline end-to-end replay without cloud side effects.

### 9.2 Compilation and command-line checks

The revised script and test file passed Python compilation. `--help` and `--version` executed successfully. Invalid configurations return defined exit codes rather than unhandled exceptions.

### 9.3 Standard-library-only offline smoke replay

The final script was run with `python -S` against two local synthetic daily datasets. This verifies that local mode does not require site packages. The replay completed with exit code 0 and produced:

- three completed round trips;
- seven transaction legs;
- one open position;
- gross and liquidation-equivalent final equity;
- fees, realized P&L, unrealized P&L, open risk, Sharpe, and drawdown metrics;
- canonical and compatibility summary files;
- canonical and compatibility completed-trade files;
- an equity curve with all documented columns;
- an immutable per-run directory;
- no network request and no Google Sheets operation.

The synthetic results are a functional check, not evidence of strategy performance.

### 9.4 Static safety review

The revised source contains:

- no embedded brokerage credentials;
- no brokerage order submission;
- no `eval()` or dynamic code execution;
- no shell invocation with `shell=True`;
- no hard-coded personal Google account or Sheet identifier;
- no cloud side effect unless publication is explicitly requested.

### 9.5 Paths not runtime-tested in this environment

The following were code-reviewed but not exercised with production credentials or data:

- live TradingView/RapidAPI retrieval with a real key;
- the installed `gog sheets` command against a real Google Sheet;
- the user's production Quiver, optimized-entry, DEA, and exchange-cache files;
- large multi-symbol or intraday production datasets.

These should first be exercised in a nonproduction or shadow environment.

---

## 10. Remaining limitations and recommended next work

### 10.1 OHLC bars cannot resolve every event sequence

Even with explicit policies, a bar does not reveal the exact path taken by price. The highest-value improvement is replay with lower-timeframe or tick data appropriate to the intended execution horizon.

### 10.2 RapidAPI retrieval remains count-based

The integration estimates a bar count and validates coverage, but the provider call does not accept exact start and end timestamps. A date-anchored source or archived local data remains preferable.

### 10.3 Corporate actions and symbol history

The script does not independently normalize splits, dividends, ticker changes, mergers, delistings, or adjusted-versus-unadjusted price semantics. Input data must be internally consistent.

### 10.4 Survivorship bias

The operator supplies the ticker list. Replaying today's watchlist over an older period can exclude companies that failed or were delisted. Research-grade evaluation should use a point-in-time universe.

### 10.5 Liquidity and execution capacity

The simulator does not model:

- partial fills;
- queue priority;
- bid/ask spread as a separate series;
- market impact;
- participation rate relative to volume;
- trading halts;
- limit-up or limit-down restrictions.

The slippage basis-point input is a simplified approximation.

### 10.6 Factor provenance

Snapshot mode prevents selection of a future-dated record, but it cannot prove that the contents of a dated file were generated solely from information available on that date. Snapshot production should be immutable, timestamped, and auditable.

### 10.7 Exchange calendars and sessions

The engine uses timestamps and a replay interval but does not enforce an exchange calendar, holiday schedule, or regular-versus-extended-hours policy. These should be added before serious intraday research.

### 10.8 Strategy validation

Correcting implementation defects does not establish positive expectancy. Recommended next steps are:

1. Establish an `off`-factor baseline.
2. Add point-in-time factor snapshots and measure incremental value.
3. Compare `conservative`, `optimistic`, `ohlc`, and `olhc` assumptions.
4. Repeat with lower-timeframe data.
5. Calibrate commissions and slippage by liquidity bucket.
6. Use walk-forward periods and untouched out-of-sample data.
7. Review maximum adverse excursion, exposure, turnover, and concentration in addition to return and Sharpe.
8. Shadow-run any production decision path before allowing automated action.

---

## 11. Deliverables

The revision package contains:

- `kurt_sandbox_v2.py` — revised replay engine;
- `test_kurt_sandbox_v2.py` — automated test suite;
- `README_kurt_sandbox_v2.md` — installation and operating guidance;
- `requirements_kurt_sandbox_v2.txt` — optional RapidAPI dependency;
- `Kurt_Sandbox_v2_Revision_Report.md` — this report;
- `Kurt_Sandbox_v2_Revision_Report.docx` — formatted report.

### File integrity

At final code validation, the principal files had these SHA-256 digests:

```text
Original script:
d80d1b561f31e48c0e511f6b6b90d86d04cb11a569a15f5a13da03a513051f9c

Revised script:
39cc93316036cc18c92fe60373bd47c207090cd00ef829c45f36b1beb40e343b

Automated tests:
cc551b1ea5954f12659b27d9dd86b026f68cecb153ab5677ffe0d51fe74f9b60
```

The revised and test digests should be recalculated if either file is edited later.

---

## 12. Conclusion

The original script embodied a plausible strategy workflow but could not support dependable performance conclusions because future information, favorable intrabar assumptions, incorrect trade pairing, risk-limit exceptions, and unintended cloud side effects affected the simulation.

Version 2.0 corrects those implementation defects while preserving the strategy's broad intent. It is suitable for controlled historical replay, sensitivity analysis, and further strategy research—especially with local point-in-time data. It should not be treated as proof of profitability or as a tick-accurate execution simulator.

The next highest-value work is to run walk-forward replay on representative point-in-time data, measure sensitivity to intrabar assumptions, and calibrate execution friction from observed fills.
