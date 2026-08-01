# JIT Transition-Matrix Regime Diagnostic
## Version 2.0 Upgrade Report

**Prepared for:** Kurt Richardson  
**Date:** July 23, 2026  
**Reviewed artifact:** `JIT Execution.zip`  
**Primary updated implementation:** `transition_matrix_live.py`

---

## Executive summary

The supplied script was a useful proof of concept for comparing first-order return-state transition matrices, but it was not sufficiently controlled for use as a hard pre-trade gate. Its largest correctness defect was that it calculated returns and state transitions across overnight session boundaries and across missing intraday bars. It also compared a partial current session with several complete prior sessions, interpreted one uncalibrated JSD value through a fixed `0.15` threshold, and applied the same block logic to risk-increasing and risk-reducing orders.

Version 2.0 replaces that design with a session-aware, matched-window diagnostic. It uses fully closed one-minute bars, excludes overnight and gap-spanning transitions, compares the live rolling window with the same minute-of-session interval from prior sessions, applies robust intraday normalization, adapts the state count when quantile boundaries collapse, and shrinks sparse live rows toward the observed baseline rather than a uniform distribution.

The revised script now reports four complementary anomaly measures: state-occupancy JSD, conditional-transition JSD, joint-transition JSD, and live-sequence surprise under the baseline model. It also reports empirical conditional entropy, approximate posterior uncertainty, directional tail changes, and the transition cells that contribute most to divergence.

Most importantly, JSD is no longer treated as the trade decision. The execution policy considers historical calibration, anomaly severity, order direction, and whether the requested order increases or reduces risk. Risk-reducing orders are not automatically blocked by model uncertainty. Structured JSON, explicit exit codes, an analysis identifier, timestamps, and a short result time-to-live support safer integration.

The implementation passed 12 automated tests and a command-line replay smoke test. The core model and local-file path were executed. Live Yahoo Finance retrieval and the optional Databento DBN adapter were not runtime-tested in the build environment because the associated packages/data were not present; both paths use lazy imports and return explicit dependency or source errors.

---

## 1. Scope of work

The update included:

1. A complete rewrite of `transition_matrix_live.py` while preserving its command-line role.
2. A 12-test standard-library test suite.
3. Dependency manifests for core, Databento, and Parquet operation.
4. A new `README.md` with installation, usage, integration, and decision semantics.
5. A narrowly scoped revision to the final JIT section of `MEMORY.md`; all preceding memory content was preserved.
6. This detailed design and verification report.

The original script was 197 lines. Version 2.0 is deliberately more comprehensive because data acquisition, session integrity, statistical estimation, decision policy, output contracts, and error handling are now explicit rather than implicit.

---

## 2. Original design risks

### 2.1 Overnight returns and cross-session transitions

The original code used a global `shift(1)` over all bars and then passed the resulting state arrays directly to the transition counter. This treated the previous close-to-next-open gap as a one-minute return and counted a transition from the last state of one session to the first state of the next.

**Why this matters:** Overnight gaps are generated over many hours and through a different information and liquidity regime. Mixing them with one-minute intraday returns changes state boundaries and adds transitions that did not occur over the claimed time interval.

### 2.2 Missing-minute bars were treated as adjacent observations

The original transition loop used array adjacency rather than timestamp adjacency. If an illiquid ticker had no bar for several minutes, the next observed state was still counted as a one-minute transition.

**Why this matters:** A five-minute or ten-minute move has a different return distribution and market meaning from a one-minute move. The error is especially important for small-cap and thinly traded stocks.

### 2.3 Time-of-day mismatch

The original baseline generally contained several complete days, while the live sample contained the latest day up to the current time. A morning run therefore compared opening activity with a baseline dominated by the full intraday volatility curve.

**Why this matters:** Equity volatility and liquidity are strongly time dependent. An opening-window difference can be normal rather than a structural break.

### 2.4 Unvalidated binary threshold

The original `JSD > 0.15` rule was not calibrated by ticker, time of day, sample size, liquidity, order type, or realized strategy outcome.

**Why this matters:** A binary rule creates false precision. A result of `0.149` and `0.151` should not produce opposite execution outcomes without evidence that the threshold has stable predictive or protective value.

### 2.5 JSD was asked to provide direction and cause

The original text inferred trend expansion, volatility breaks, institutional positioning, and order-book mutation from one-minute closing returns.

**Why this matters:** Divergence measures difference, not cause. Closing-price bars do not identify institutional participants or reveal order-book structure.

### 2.6 Risk-increasing and risk-reducing actions were treated alike

The original memory directive blocked both capital deployment and profit-taking when the JSD threshold was exceeded.

**Why this matters:** A model anomaly should not prevent an urgent stop-loss or other risk-reducing transaction. The policy must distinguish the effect of an order on exposure.

### 2.7 Human-readable output was the only integration contract

The original script printed text, returned success even after a high-divergence warning, and did not produce a structured decision, freshness timestamp, or distinct status code.

**Why this matters:** Console text is brittle for automation. A downstream process needs explicit semantics and must distinguish a detected risk condition from missing, stale, or invalid data.

---

## 3. Version 2.0 architecture

The updated processing flow is:

```text
Market-data provider
        |
Canonical UTC one-minute bars
        |
Exchange-calendar session assignment
        |
Closed-bar, duplicate, price, freshness, and gap validation
        |
Within-session contiguous close-to-close returns
        |
Rolling live window and matched prior-session windows
        |
Robust minute-of-session volatility normalization
        |
Adaptive quantile-state model fitted on baseline only
        |
Session- and gap-aware transition counts
        |
Baseline matrix + baseline-centered live shrinkage
        |
Occupancy JSD + conditional JSD + joint JSD + sequence surprise
        |
Historical calibration + uncertainty + directional tail analysis
        |
Order-specific decision policy
        |
Text or versioned JSON + exit code + short time-to-live
```

The script remains a single executable file to simplify deployment into the existing `/root/.openclaw/workspace/scripts/` location. Its internal functions and dataclasses are separated sufficiently to support unit testing and later modularization.

---

## 4. Detailed changes and justification

### 4.1 Session- and gap-safe return construction

**Implementation**

- Bars are converted to a timezone-aware UTC index.
- An exchange calendar, `XNYS` by default, assigns each regular-session bar to a session and minute-of-session.
- Returns are calculated only when adjacent bars belong to the same session and are exactly one minute apart.
- Transition counts independently recheck the session and timestamp conditions.

**Justification**

This creates defense in depth. An overnight gap or missing-minute interval cannot enter the one-minute return series, and a later transformation cannot accidentally reconnect two separated state sequences.

### 4.2 Fully closed bars and live freshness

**Implementation**

- A bar is excluded until its one-minute interval has closed plus a configurable completion lag.
- Live mode verifies that the configured exchange is open.
- The newest bar must belong to the current session.
- The age of the newest closed bar is compared with `--stale-after-minutes`.
- Separate outcomes exist for `MARKET_CLOSED` and `STALE_DATA`.

**Justification**

A partially formed bar can change before execution. A stale value can make a mathematically correct result operationally irrelevant. These are data-quality states, not market-regime signals, and therefore receive separate decisions and exit codes.

### 4.3 Removal of the 80/20 fallback

**Implementation**

The script now requires at least two sessions and a configurable number of adequately covered prior matched sessions. It returns `INSUFFICIENT_DATA` rather than silently changing the analysis definition.

**Justification**

An 80/20 split of one session is not equivalent to a historical-versus-live session comparison. Silent semantic changes are dangerous in an execution control.

### 4.4 Rolling, time-matched windows

**Implementation**

- The live window is the most recent configurable number of minute-of-session returns; the default is 60.
- Each baseline session contributes the same minute-of-session range.
- Sessions that do not meet the configured coverage fraction are rejected.
- Baseline and live transition counts are reported.

**Justification**

This controls for the normal intraday volatility and liquidity cycle and keeps the diagnostic responsive to recent conditions rather than diluting them with the entire current day.

### 4.5 Robust intraday volatility normalization

**Implementation**

- The baseline is divided into configurable minute-of-session buckets, 15 minutes by default.
- Each bucket uses a robust median and scale based primarily on median absolute deviation.
- Sparse or degenerate buckets fall back to a robust global baseline scale.
- The model clips extreme normalized values to a configurable range and reports fallback buckets.

**Justification**

A raw return of a given magnitude has different significance near the open and at midday. Robust scaling reduces sensitivity to outliers and makes state meanings more comparable through the session.

### 4.6 Adaptive state resolution

**Implementation**

The state discretizer starts with the requested number of quantile states and reduces the count when boundaries are duplicated or any state has insufficient baseline support. The effective state count and boundaries are returned.

**Justification**

Thinly traded stocks often have many zero or repeated returns. Pretending that eight distinct states exist when several quantiles are identical produces empty or redundant rows and unstable metrics.

### 4.7 Correct origin-state occupancy

**Implementation**

Occupancy is calculated from states that actually serve as transition origins. The final state in a sequence is not counted unless it is followed by a valid next observation.

**Justification**

Transition-row weighting must use the same sample that generated the transition counts. This matters most in short live windows.

### 4.8 Baseline-centered shrinkage

**Implementation**

- The baseline matrix uses a small symmetric Dirichlet smoothing value.
- Sparse live rows are shrunk toward the corresponding historical baseline row.
- Live occupancy and joint distributions also use baseline-centered priors.

**Justification**

The original uniform prior implied that an unobserved live row was equally likely to transition anywhere. A baseline-centered prior expresses the more defensible assumption that behavior remains near historical norms until live evidence supports a change.

### 4.9 Multiple divergence views

**Implementation**

The result reports:

1. **State-occupancy JSD** - change in how frequently states occur.
2. **Conditional-transition JSD** - change in next-state probabilities given an origin state.
3. **Joint-transition JSD** - change in the complete origin/destination distribution.

Conditional JSD uses symmetric occupancy weights rather than live occupancy alone.

**Justification**

A stock can spend much more time in negative states even if conditional rows remain similar, or it can preserve occupancy while changing transition grammar. Separate metrics expose these different forms of change.

### 4.10 Sequence surprise

**Implementation**

The script calculates the mean negative log base-2 probability of the actual live transitions under the baseline matrix.

**Justification**

Matrix-to-matrix divergence estimates two distributions. Sequence surprise asks a complementary question: how improbable was the observed live path under the baseline model? It is naturally expressed in bits per transition and can later support sequential change detection.

### 4.11 Entropy terminology correction

**Implementation**

The reported quantity is now named **empirical conditional transition entropy** rather than an unqualified entropy rate.

**Justification**

The calculation is weighted by observed origin-state occupancy. A formal stationary Markov entropy rate normally uses the chain's stationary distribution. The new name accurately describes the implemented statistic.

### 4.12 Approximate leave-one-session-out calibration

**Implementation**

Each historical matched session is treated as a pseudo-live sample while its transition counts are excluded from the comparison baseline. The script builds empirical distributions for occupancy JSD, conditional JSD, joint JSD, and sequence surprise. The current result is converted to an out-of-sample rank percentile.

**Justification**

The policy now asks whether the current reading is unusual for this ticker, time window, state model, and sample structure rather than comparing it with a universal constant.

**Qualification**

Normalization and state boundaries remain fixed from the full historical baseline to maintain a consistent state definition. This makes the calibration approximate. A fully nested walk-forward refit is appropriate for research validation but would add complexity and computation to every JIT call.

### 4.13 Uncertainty reporting

**Implementation**

The script can draw Dirichlet posterior samples for the baseline and live joint-transition distributions and report the median and 95% interval of the resulting JSD distribution.

**Justification**

A point estimate based on 20 transitions should not be interpreted like one based on 300. Posterior spread exposes uncertainty caused by sparse cells.

**Interpretation note**

The JSD of posterior mean distributions need not equal the median JSD of posterior draws. The interval therefore may not be centered on the point estimate; this is a consequence of the nonlinear divergence function, not necessarily an error.

### 4.14 Directional tail context

**Implementation**

The script estimates baseline median raw return for each state and reports:

- expected next return under baseline and live models;
- expected next return from the current state;
- negative- and positive-tail transition probability;
- changes in those probabilities;
- negative- and positive-tail persistence.

It classifies evidence as downside, upside, two-sided volatility expansion, or neutral/mixed.

**Justification**

A high JSD only indicates difference. Directional context is required before deciding whether a change is adverse to a long or short entry.

### 4.15 Order-specific execution policy

**Implementation**

Risk-increasing actions are `new-long`, `add-long`, and `new-short`. Risk-reducing actions are `sell-profit`, `stop-loss`, `reduce-long`, and `cover-short`.

An automatic `BLOCK_NEW_RISK` requires:

1. adequate calibration history;
2. an extreme composite anomaly percentile; and
3. directional evidence adverse to the proposed risk-increasing action.

Risk-reducing actions can return `ALLOW_RISK_REDUCTION` even when the model warns.

**Justification**

The decision should reflect the consequence of the order. Uncertain or adverse conditions can justify refusing new exposure without trapping the portfolio in an existing exposure.

### 4.16 Claim discipline

**Implementation**

All output describes return-transition behavior. References to institutional positioning and order-book mutation were removed from the script and revised memory section.

**Justification**

The available data does not support causal or participant-level conclusions. Accurate language reduces the risk of false confidence.

### 4.17 Data-provider abstraction

**Implementation**

The command line supports:

- Yahoo Finance one-minute data for prototype/research operation;
- CSV and compressed CSV;
- JSON Lines;
- Parquet when a compatible engine is installed;
- local Databento DBN or DBN.ZST through the optional official Python client.

The Databento path uses `DBNStore.from_file(...).to_df(...)` and requires an OHLCV schema containing a close field. The official Databento documentation identifies `DBNStore.from_file` as the local DBN reader and demonstrates conversion to a DataFrame [1]. The current yfinance API documents one-minute interval retrieval through `download` and notes that intraday history is bounded [2].

**Justification**

This allows previously purchased/downloaded data to be replayed without another download and isolates the model from one prototype provider. The script intentionally does not embed credentials.

### 4.18 Structured output, exit codes, and freshness contract

**Implementation**

JSON includes:

- schema and script versions;
- analysis ID;
- ticker, action, source, and mode;
- analysis time, as-of time, last bar time, and validity deadline;
- data-quality diagnostics;
- model and window details;
- metrics, calibration, direction, contributors, reasons, and warnings;
- decision and exit code.

**Justification**

A downstream service can make explicit decisions, reject expired or mismatched analyses, and record overrides. Text remains available for human review.

### 4.19 Contributor diagnostics

**Implementation**

The script lists the origin/destination cells contributing most to joint-transition JSD, including baseline probability, live probability, change, and contribution.

**Justification**

A scalar divergence is difficult to audit. Cell-level contributors show whether the anomaly is driven by persistence, tail entry, tail escape, or other transition changes.

### 4.20 Software engineering and failure isolation

**Implementation**

- Typed dataclasses and enums.
- Explicit custom error classes.
- Lazy optional imports.
- Narrow source-specific error reporting.
- Versioned output schema.
- Config validation.
- No shell execution, credential access, file mutation beyond an explicitly requested output, or order placement.
- Unit-testable public analysis entry point.

**Justification**

A pre-trade control must fail predictably and must not blur a market warning with a dependency, calendar, or input error.

---

## 5. Decision and exit-code contract

| Decision | Exit code | Meaning |
|---|---:|---|
| `PASS` | 0 | Matched-window diagnostics are within the calibrated range. Normal execution controls may continue. |
| `ALLOW_RISK_REDUCTION` | 0 | A warning may exist, but the requested action reduces exposure. |
| `CAUTION` | 10 | Do not automatically increase risk; require human review or explicit override. |
| `BLOCK_NEW_RISK` | 20 | Block the risk-increasing order unless an audited override is provided. |
| `INSUFFICIENT_DATA` | 30 | No valid analysis; fail closed for new risk. |
| `STALE_DATA` | 31 | Latest closed bar is too old; fail closed for new risk. |
| `MARKET_CLOSED` | 32 | Configured exchange is closed in live mode. |
| `DATA_SOURCE_ERROR` | 40 | Input, network, format, or dependency failure. |
| `INVALID_ARGUMENT` | 41 | Configuration is invalid. |
| `INTERNAL_ERROR` | 50 | Unexpected software failure. |

The normal trade confirmation, live quote, share-count, and queue-injection controls remain separate. This script neither submits nor modifies orders.

---

## 6. Representative command lines

### 6.1 Prototype live diagnostic

```bash
python3 transition_matrix_live.py \
  --ticker MCD \
  --source yfinance \
  --mode live \
  --action new-long \
  --format json \
  --compact-json
```

### 6.2 Replay an existing Databento OHLCV file

```bash
python3 transition_matrix_live.py \
  --ticker MCD \
  --source dbn \
  --input MCD.ohlcv-1m.dbn.zst \
  --mode replay \
  --as-of 2026-07-22T15:30:00Z \
  --window-minutes 60 \
  --baseline-sessions 20 \
  --format json \
  --output results/MCD_20260722T1530Z.json
```

### 6.3 Evaluate a stop-loss context

```bash
python3 transition_matrix_live.py \
  --ticker MCD \
  --source file \
  --input current_MCD_ohlcv_1m.csv \
  --mode live \
  --action stop-loss \
  --format json
```

Even a high anomaly does not automatically block this risk-reducing action.

---

## 7. Verification performed

### 7.1 Automated test suite

The included `unittest` suite passed all 12 tests:

| Test area | Coverage |
|---|---|
| JSD mathematics | Identity, symmetry, and boundedness. |
| Transition integrity | No transitions across sessions or timestamp gaps. |
| Occupancy | Only valid transition origins are counted. |
| State adaptation | Collapsed quantiles are rejected or state count is reduced. |
| Symbol selection | A requested ticker missing from a multi-symbol input is rejected. |
| Timestamp validation | Invalid timestamp rows are removed. |
| Calendar status | Closed-market condition receives a dedicated status. |
| Freshness | Stale live data receives a dedicated status. |
| Long-entry policy | Extreme downside evidence blocks new long risk. |
| Stop-loss policy | Model uncertainty does not block risk reduction. |
| End-to-end replay | Structured result, calibration, metrics, and contributors. |
| Overnight gap exclusion | Large overnight price level changes do not enter one-minute returns. |

Test command:

```bash
python -m unittest discover -s tests -v
```

Result:

```text
Ran 12 tests
OK
```

### 7.2 Command-line smoke test

A seven-session synthetic OHLCV file was analyzed through the actual file-source command line. The run produced valid JSON and text, 354 baseline transitions, 59 live transitions, a calibrated decision, posterior diagnostics, and five transition contributors. The process returned the same exit code recorded in JSON.

### 7.3 Static checks

- Python compilation succeeded with `py_compile` and `compileall`.
- CLI help rendered successfully.
- The revised `MEMORY.md` was verified to preserve all content before the original final JIT section.

### 7.4 Items not runtime-tested

- Live Yahoo Finance retrieval, because `yfinance` was not installed in the build environment and external execution was not required for core verification.
- Local DBN reading, because no Databento DBN sample and no Databento Python package were supplied.
- Parquet loading, because no Parquet engine was installed.

These paths fail with explicit dependency or source errors rather than falling through to a misleading market decision.

---

## 8. Revised `MEMORY.md` policy

Only the final JIT section was replaced. The revised policy:

- removes the fixed `0.15` rule;
- requires the action context and structured JSON;
- defines decisions and exit codes;
- fails closed for risk-increasing orders on missing, stale, closed-market, or source-error outcomes;
- does not block urgent risk reduction solely because the model is uncertain;
- requires audited manual overrides;
- honors the result time-to-live;
- prohibits unsupported claims about institutions or order-book causes;
- labels Yahoo Finance as a prototype source.

This change is necessary because leaving the old memory section in place would cause the autonomous agent to ignore or misinterpret the new script contract.

---

## 9. Important limitations and deferred work

### 9.1 Strategy-outcome threshold validation remains required

Version 2.0 calibrates anomaly metrics against historical matched sessions. It does not yet optimize thresholds against the trading strategy's realized maximum adverse excursion, slippage, spread, stop-out rate, or profit-and-loss outcomes.

**Recommended next step:** Run walk-forward replay over the existing Databento archive and join every JIT observation to subsequent execution-risk outcomes. Select warning and block policies based on false-pass and false-block costs rather than anomaly percentiles alone.

### 9.2 Live Databento streaming adapter is not embedded

The package reads previously downloaded DBN files. A direct live adapter requires explicit choices for dataset, schema, symbology, subscriptions, reconnect behavior, and how a continuously updated one-minute bar snapshot is exposed to this process.

**Recommended next step:** Implement a separate feed service that writes or serves canonical closed one-minute bars. Keep credentials and stream lifecycle outside the diagnostic.

### 9.3 True multiscale and higher-order models are deferred

The current live window is configurable, so the script can be run separately at 30, 60, or 120 minutes. It does not yet combine multiple window decisions, resample five- or fifteen-minute returns, or fit second-order state transitions.

**Reason for deferral:** Higher-order and multiscale models increase parameter count and multiple-testing risk. They should be added only after replay demonstrates incremental decision value.

### 9.4 Market-microstructure features are not included

The DBN adapter expects OHLCV bars. It does not use trades, BBO, MBP, MBO, spread, depth, imbalance, halt events, or aggressor-side estimates.

**Recommended next step:** Add microstructure features as separate evidence layers and test their incremental value. Do not relabel the current close-return model as an order-book detector.

### 9.5 The posterior interval is diagnostic, not a frequentist confidence interval

It reflects uncertainty under the chosen Dirichlet model and priors. It should be interpreted as model-based uncertainty and validated for calibration.

### 9.6 Small historical samples produce coarse percentiles

With four calibration sessions, possible rank percentiles are coarse. The script therefore reports calibration quality and refuses to auto-block when the minimum history requirement is not met. Production use should normally load substantially more matched sessions from the local archive.

### 9.7 Direct execution integration and cryptographic signing are outside this package

The script emits an analysis ID and TTL but does not sign the payload or call order scripts.

**Recommended next step:** The execution service should bind the result to ticker, action, side, share count, and analysis ID; reject expired or reused results; and record any override.

---

## 10. Recommended deployment sequence

1. **Replay validation:** Run the script against at least several months of local one-minute Databento OHLCV data for representative liquid and illiquid tickers.
2. **Outcome labeling:** Attach subsequent adverse excursion, spread, slippage, halt, and stop-out outcomes.
3. **Policy calibration:** Choose minimum history, window, state count, prior strength, warning percentile, block percentile, and tail-shift criteria by walk-forward validation.
4. **Shadow mode:** Run the diagnostic before live orders, record decisions, but do not block execution.
5. **Review false signals:** Compare warnings and blocks with actual outcomes and operator decisions.
6. **Controlled enforcement:** Enable blocks only for risk-increasing actions after acceptable false-pass and false-block rates are demonstrated.
7. **Feed hardening:** Replace the prototype source with a controlled live feed or feed service.
8. **Audit integration:** Persist the JSON result, order context, confirmation, execution outcome, and override information.
9. **Incremental extensions:** Test multiscale and market-microstructure features only after the first-order baseline is stable.

---

## 11. Traceability to the prior recommendations

| Prior recommendation | Version 2.0 disposition |
|---|---|
| Prevent cross-session transitions | Implemented. |
| Compare equivalent time-of-day windows | Implemented. |
| Use a rolling live window | Implemented; one configurable primary window per run. |
| Validate closed, current, contiguous bars | Implemented. |
| Remove silent 80/20 fallback | Implemented. |
| Correct occupancy | Implemented. |
| Compare complete transition distribution | Implemented with occupancy, conditional, and joint JSD. |
| Rename entropy output | Implemented. |
| Handle duplicate quantile boundaries | Implemented with adaptive state count. |
| Normalize intraday volatility | Implemented with robust buckets and fallback. |
| Use baseline rather than uniform live prior | Implemented. |
| Add statistical uncertainty | Implemented as Dirichlet posterior JSD distribution. |
| Calibrate with replay | Partially implemented for anomaly calibration; strategy-outcome calibration remains. |
| Add sequence surprise | Implemented. |
| Add direction | Implemented with expected return and tail metrics. |
| Treat buy/sell contexts differently | Implemented through action-specific policy. |
| Remove unsupported microstructure claims | Implemented. |
| Replace production dependence on yfinance | Partially implemented: local file and DBN support added; controlled live adapter remains. |
| Produce structured output and exit codes | Implemented. |
| Add TTL and audit fields | Implemented with analysis ID and validity deadline; external persistence/signing remains. |
| Add top-contributor diagnostics | Implemented. |
| Add multiscale/higher-order models | Deferred pending replay evidence. |
| General production hardening and tests | Implemented substantially. |

---

## 12. Overall assessment

Version 2.0 is a materially safer and more defensible diagnostic than the original proof of concept. The model now measures what it claims to measure: changes in one-minute return-state occupancy and transition behavior relative to matched historical windows. It also exposes data quality, sample size, calibration, uncertainty, direction, and decision rationale.

It should still enter production through replay and shadow validation rather than immediately becoming an unattended capital-allocation authority. The most important remaining work is not another mathematical feature; it is proving, with the existing Databento archive, that the resulting warnings and blocks improve the strategy's realized execution-risk outcomes.

---

## References

[1] Databento, **DBNStore.from_file**, Historical API documentation. The official example reads a local `.dbn.zst` file and converts it with `to_df()`.

[2] yfinance, **yfinance.download**, API reference. The documented interface supports one-minute intervals and identifies limits on intraday history.

[3] Lin, J. (1991), “Divergence Measures Based on the Shannon Entropy,” *IEEE Transactions on Information Theory*, 37(1), 145-151.

[4] Cover, T. M., and Thomas, J. A. (2006), *Elements of Information Theory*, 2nd ed., Wiley.
