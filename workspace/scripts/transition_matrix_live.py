#!/usr/bin/env python3
"""
JIT Transition-Matrix Regime Diagnostic, version 2.0.

This program analyzes closed one-minute bars without crossing session or data-gap
boundaries. It compares a rolling live window with the same minute-of-session
window from prior sessions, then reports several complementary diagnostics:

* state-occupancy Jensen-Shannon divergence (JSD)
* conditional-transition JSD
* joint-transition JSD
* live-sequence surprise under the baseline Markov model
* empirical conditional transition entropy
* directional tail-risk changes

The output is a diagnostic and execution-policy input, not a price forecast. A
large divergence means that the observed return-transition behavior is unusual
relative to the selected historical baseline; it does not identify the cause.

Examples
--------
Live Yahoo Finance prototype source:

    python transition_matrix_live.py --ticker MCD --source yfinance \
        --mode live --action new-long --format json

Previously downloaded Databento OHLCV DBN data:

    python transition_matrix_live.py --ticker MCD --source dbn \
        --input MCD.ohlcv-1m.dbn.zst --mode replay \
        --as-of 2026-07-22T15:30:00Z --format json

CSV or Parquet with a timestamp/ts_event column and close column:

    python transition_matrix_live.py --ticker MCD --source file \
        --input MCD_ohlcv_1m.csv --mode replay --format text

Exit codes
----------
0   PASS or ALLOW_RISK_REDUCTION
10  CAUTION / human review required
20  BLOCK_NEW_RISK
30  INSUFFICIENT_DATA
31  STALE_DATA
32  MARKET_CLOSED
40  DATA_SOURCE_ERROR
41  INVALID_ARGUMENT
50  INTERNAL_ERROR

Author attribution retained from the supplied prototype: Kurt Richardson.
Version 2 redesign and hardening: July 23, 2026.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import traceback
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum, IntEnum
from pathlib import Path
from typing import Any, Iterable, Protocol, Sequence

import numpy as np
import pandas as pd

try:
    import exchange_calendars as xcals
except ModuleNotFoundError:  # handled with a clear runtime error
    xcals = None  # type: ignore[assignment]


SCRIPT_VERSION = "2.0.0"
SCHEMA_VERSION = "2.0"
BAR_DURATION = pd.Timedelta(minutes=1)
UTC = "UTC"


class ExitCode(IntEnum):
    OK = 0
    CAUTION = 10
    BLOCK_NEW_RISK = 20
    INSUFFICIENT_DATA = 30
    STALE_DATA = 31
    MARKET_CLOSED = 32
    DATA_SOURCE_ERROR = 40
    INVALID_ARGUMENT = 41
    INTERNAL_ERROR = 50


class Action(str, Enum):
    DIAGNOSTIC = "diagnostic"
    NEW_LONG = "new-long"
    ADD_LONG = "add-long"
    NEW_SHORT = "new-short"
    SELL_PROFIT = "sell-profit"
    STOP_LOSS = "stop-loss"
    REDUCE_LONG = "reduce-long"
    COVER_SHORT = "cover-short"

    @property
    def is_risk_reducing(self) -> bool:
        return self in {
            Action.SELL_PROFIT,
            Action.STOP_LOSS,
            Action.REDUCE_LONG,
            Action.COVER_SHORT,
        }

    @property
    def is_long_risk_increasing(self) -> bool:
        return self in {Action.NEW_LONG, Action.ADD_LONG}

    @property
    def is_short_risk_increasing(self) -> bool:
        return self is Action.NEW_SHORT


class RunMode(str, Enum):
    LIVE = "live"
    REPLAY = "replay"


class Decision(str, Enum):
    PASS = "PASS"
    CAUTION = "CAUTION"
    BLOCK_NEW_RISK = "BLOCK_NEW_RISK"
    ALLOW_RISK_REDUCTION = "ALLOW_RISK_REDUCTION"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    STALE_DATA = "STALE_DATA"
    MARKET_CLOSED = "MARKET_CLOSED"
    DATA_SOURCE_ERROR = "DATA_SOURCE_ERROR"
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class AnalysisError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        decision: Decision,
        exit_code: ExitCode,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.decision = decision
        self.exit_code = exit_code
        self.details = details or {}


class InsufficientDataError(AnalysisError):
    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(
            message,
            decision=Decision.INSUFFICIENT_DATA,
            exit_code=ExitCode.INSUFFICIENT_DATA,
            details=details,
        )


class StaleDataError(AnalysisError):
    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(
            message,
            decision=Decision.STALE_DATA,
            exit_code=ExitCode.STALE_DATA,
            details=details,
        )


class MarketClosedError(AnalysisError):
    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(
            message,
            decision=Decision.MARKET_CLOSED,
            exit_code=ExitCode.MARKET_CLOSED,
            details=details,
        )


class DataSourceError(AnalysisError):
    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(
            message,
            decision=Decision.DATA_SOURCE_ERROR,
            exit_code=ExitCode.DATA_SOURCE_ERROR,
            details=details,
        )


class InvalidArgumentError(AnalysisError):
    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(
            message,
            decision=Decision.INVALID_ARGUMENT,
            exit_code=ExitCode.INVALID_ARGUMENT,
            details=details,
        )


@dataclass(frozen=True)
class AnalysisConfig:
    ticker: str
    action: Action = Action.DIAGNOSTIC
    mode: RunMode = RunMode.LIVE
    calendar_name: str = "XNYS"
    requested_states: int = 8
    window_minutes: int = 60
    baseline_sessions: int = 20
    min_baseline_sessions: int = 4
    min_live_transitions: int = 20
    min_session_coverage: float = 0.80
    alpha: float = 0.5
    live_prior_strength: float = 5.0
    volatility_normalization: bool = True
    volatility_bucket_minutes: int = 15
    min_bucket_samples: int = 8
    z_clip: float = 12.0
    min_state_samples: int = 5
    tail_fraction: float = 0.25
    adverse_tail_shift: float = 0.05
    warning_percentile: float = 95.0
    block_percentile: float = 99.0
    min_calibration_sessions: int = 4
    posterior_samples: int = 500
    random_seed: int = 1729
    stale_after_minutes: float = 3.0
    completion_lag_seconds: float = 2.0
    result_ttl_seconds: int = 90
    top_transitions: int = 5

    def validate(self) -> None:
        if not self.ticker.strip():
            raise InvalidArgumentError("Ticker cannot be blank.")
        if not 3 <= self.requested_states <= 20:
            raise InvalidArgumentError("--states must be between 3 and 20.")
        if self.window_minutes < 5:
            raise InvalidArgumentError("--window-minutes must be at least 5.")
        if self.baseline_sessions < 1:
            raise InvalidArgumentError("--baseline-sessions must be positive.")
        if not 1 <= self.min_baseline_sessions <= self.baseline_sessions:
            raise InvalidArgumentError(
                "--min-baseline-sessions must be between 1 and --baseline-sessions."
            )
        if self.min_live_transitions < 2:
            raise InvalidArgumentError("--min-live-transitions must be at least 2.")
        if not 0.0 < self.min_session_coverage <= 1.0:
            raise InvalidArgumentError("--min-session-coverage must be in (0, 1].")
        if self.alpha <= 0.0:
            raise InvalidArgumentError("--alpha must be greater than zero.")
        if self.live_prior_strength <= 0.0:
            raise InvalidArgumentError("--prior-strength must be greater than zero.")
        if self.volatility_bucket_minutes < 1:
            raise InvalidArgumentError(
                "--volatility-bucket-minutes must be positive."
            )
        if self.min_bucket_samples < 2:
            raise InvalidArgumentError("--min-bucket-samples must be at least 2.")
        if self.z_clip <= 0.0:
            raise InvalidArgumentError("--z-clip must be greater than zero.")
        if self.min_state_samples < 1:
            raise InvalidArgumentError("--min-state-samples must be positive.")
        if not 0.0 < self.tail_fraction < 0.5:
            raise InvalidArgumentError("--tail-fraction must be in (0, 0.5).")
        if self.adverse_tail_shift < 0.0:
            raise InvalidArgumentError("--adverse-tail-shift cannot be negative.")
        if not 50.0 <= self.warning_percentile < self.block_percentile < 100.0:
            raise InvalidArgumentError(
                "Percentiles must satisfy 50 <= warning < block < 100."
            )
        if self.min_calibration_sessions < 2:
            raise InvalidArgumentError(
                "--min-calibration-sessions must be at least 2."
            )
        if self.posterior_samples < 0:
            raise InvalidArgumentError("--posterior-samples cannot be negative.")
        if self.stale_after_minutes <= 0.0:
            raise InvalidArgumentError(
                "--stale-after-minutes must be greater than zero."
            )
        if self.completion_lag_seconds < 0.0:
            raise InvalidArgumentError(
                "--completion-lag-seconds cannot be negative."
            )
        if self.result_ttl_seconds <= 0:
            raise InvalidArgumentError("--ttl-seconds must be positive.")
        if self.top_transitions < 0:
            raise InvalidArgumentError("--top-transitions cannot be negative.")


@dataclass
class DataQuality:
    rows_loaded: int = 0
    duplicate_timestamps_removed: int = 0
    invalid_close_rows_removed: int = 0
    rows_after_as_of_removed: int = 0
    incomplete_bars_removed: int = 0
    outside_regular_session_removed: int = 0
    valid_regular_session_bars: int = 0
    within_session_gaps: int = 0
    valid_return_rows: int = 0
    last_bar_start_utc: str | None = None
    last_bar_end_utc: str | None = None
    last_bar_age_seconds: float | None = None
    current_session: str | None = None


@dataclass
class WindowData:
    live: pd.DataFrame
    baseline: pd.DataFrame
    baseline_by_session: dict[str, pd.DataFrame]
    live_session: str
    start_minute_of_session: int
    end_minute_of_session: int
    expected_returns_per_session: int
    baseline_sessions_available: int
    baseline_sessions_used: list[str]
    rejected_baseline_sessions: list[str]
    live_coverage: float


@dataclass
class TransitionStats:
    counts: np.ndarray
    origin_counts: np.ndarray
    origins: np.ndarray
    destinations: np.ndarray
    valid_transition_count: int


class MarketDataProvider(Protocol):
    name: str

    def load(self, ticker: str) -> tuple[pd.DataFrame, dict[str, Any]]:
        """Return a canonicalizable market-data frame and source metadata."""


class YFinanceProvider:
    name = "yfinance"

    def __init__(self, period: str = "5d", timeout_seconds: float = 20.0) -> None:
        self.period = period
        self.timeout_seconds = timeout_seconds

    def load(self, ticker: str) -> tuple[pd.DataFrame, dict[str, Any]]:
        try:
            import yfinance as yf
        except ModuleNotFoundError as exc:
            raise DataSourceError(
                "yfinance is not installed. Install requirements.txt or use a local file source.",
                missing_dependency="yfinance",
            ) from exc

        try:
            frame = yf.download(
                tickers=ticker,
                period=self.period,
                interval="1m",
                actions=False,
                auto_adjust=False,
                repair=False,
                keepna=False,
                prepost=False,
                progress=False,
                threads=False,
                timeout=self.timeout_seconds,
                multi_level_index=False,
            )
        except Exception as exc:  # library/network exceptions vary by version
            raise DataSourceError(
                f"Yahoo Finance download failed for {ticker}: {exc}",
                provider=self.name,
            ) from exc

        if frame is None or frame.empty:
            raise DataSourceError(
                f"No one-minute data was returned for {ticker}.",
                provider=self.name,
                period=self.period,
            )

        return frame, {
            "provider": self.name,
            "period": self.period,
            "interval": "1m",
            "intended_use": "prototype/research source",
        }


class LocalFileProvider:
    name = "local-file"

    def __init__(
        self,
        path: Path,
        *,
        timestamp_column: str | None = None,
        symbol_column: str | None = None,
        input_timezone: str = "America/New_York",
    ) -> None:
        self.path = path
        self.timestamp_column = timestamp_column
        self.symbol_column = symbol_column
        self.input_timezone = input_timezone

    def load(self, ticker: str) -> tuple[pd.DataFrame, dict[str, Any]]:
        if not self.path.exists():
            raise DataSourceError(
                f"Input file does not exist: {self.path}", path=str(self.path)
            )

        lower_name = self.path.name.lower()
        try:
            if lower_name.endswith((".csv", ".csv.gz", ".csv.zst")):
                frame = pd.read_csv(self.path)
                encoding = "csv"
            elif lower_name.endswith((".json", ".jsonl", ".json.gz", ".json.zst")):
                frame = pd.read_json(self.path, lines=True)
                encoding = "json-lines"
            elif lower_name.endswith((".parquet", ".pq")):
                frame = pd.read_parquet(self.path)
                encoding = "parquet"
            else:
                raise DataSourceError(
                    "Unsupported local file type. Use CSV, JSON Lines, Parquet, or --source dbn.",
                    path=str(self.path),
                )
        except AnalysisError:
            raise
        except Exception as exc:
            raise DataSourceError(
                f"Unable to read {self.path}: {exc}", path=str(self.path)
            ) from exc

        return frame, {
            "provider": self.name,
            "path": str(self.path.resolve()),
            "encoding": encoding,
            "timestamp_column": self.timestamp_column,
            "symbol_column": self.symbol_column,
            "input_timezone": self.input_timezone,
            "ticker_filter": ticker,
        }


class DatabentoDbnProvider:
    name = "databento-dbn"

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self, ticker: str) -> tuple[pd.DataFrame, dict[str, Any]]:
        if not self.path.exists():
            raise DataSourceError(
                f"DBN input file does not exist: {self.path}", path=str(self.path)
            )
        try:
            import databento as db
        except ModuleNotFoundError as exc:
            raise DataSourceError(
                "The databento package is required for --source dbn. "
                "Install requirements-databento.txt.",
                missing_dependency="databento",
            ) from exc

        try:
            store = db.DBNStore.from_file(self.path)
            frame = store.to_df(
                price_type="float",
                pretty_ts=True,
                map_symbols=True,
                tz=UTC,
            )
        except Exception as exc:
            raise DataSourceError(
                f"Unable to read Databento DBN file {self.path}: {exc}",
                path=str(self.path),
            ) from exc

        if frame is None or frame.empty:
            raise DataSourceError(
                f"The Databento DBN file contains no rows: {self.path}",
                path=str(self.path),
            )

        return frame, {
            "provider": self.name,
            "path": str(self.path.resolve()),
            "encoding": "dbn",
            "ticker_filter": ticker,
        }


def _find_column(frame: pd.DataFrame, candidates: Iterable[str]) -> str | None:
    lookup = {str(column).strip().lower(): str(column) for column in frame.columns}
    for candidate in candidates:
        matched = lookup.get(candidate.strip().lower())
        if matched is not None:
            return matched
    return None


def _flatten_single_ticker_columns(frame: pd.DataFrame, ticker: str) -> pd.DataFrame:
    if not isinstance(frame.columns, pd.MultiIndex):
        return frame

    for level in range(frame.columns.nlevels):
        values = frame.columns.get_level_values(level).astype(str)
        if ticker.upper() in {value.upper() for value in values}:
            matching_value = next(
                value for value in values if value.upper() == ticker.upper()
            )
            try:
                return frame.xs(matching_value, axis=1, level=level, drop_level=True)
            except KeyError:
                pass

    flattened = frame.copy()
    flattened.columns = [
        "_".join(str(part) for part in column if str(part))
        for column in flattened.columns
    ]
    return flattened


def canonicalize_market_frame(
    raw_frame: pd.DataFrame,
    *,
    ticker: str,
    timestamp_column: str | None = None,
    symbol_column: str | None = None,
    input_timezone: str = "America/New_York",
) -> pd.DataFrame:
    """Convert common Yahoo/Databento/file layouts to UTC-indexed close bars."""
    if raw_frame is None or raw_frame.empty:
        raise DataSourceError("The market-data frame is empty.")

    frame = _flatten_single_ticker_columns(raw_frame.copy(), ticker)

    resolved_symbol_column = symbol_column or _find_column(
        frame, ["symbol", "ticker", "raw_symbol"]
    )
    if resolved_symbol_column is not None:
        symbol_values = frame[resolved_symbol_column].astype(str).str.upper()
        matching = symbol_values == ticker.upper()
        if not matching.any():
            raise DataSourceError(
                f"Ticker {ticker} was not present in symbol column {resolved_symbol_column}."
            )
        frame = frame.loc[matching].copy()

    resolved_timestamp = timestamp_column or _find_column(
        frame,
        [
            "ts_event",
            "timestamp",
            "datetime",
            "date_time",
            "time",
            "date",
        ],
    )

    if resolved_timestamp is not None:
        timestamp_values = frame[resolved_timestamp]
        if pd.api.types.is_datetime64_any_dtype(timestamp_values):
            timestamps = pd.to_datetime(timestamp_values, errors="coerce")
        else:
            timestamps = pd.to_datetime(
                timestamp_values, errors="coerce", format="mixed"
            )
    elif isinstance(frame.index, pd.DatetimeIndex):
        timestamps = pd.DatetimeIndex(frame.index)
    else:
        raise DataSourceError(
            "No timestamp column or DatetimeIndex was found. Use --timestamp-column."
        )

    timestamps = pd.DatetimeIndex(timestamps)
    valid_timestamp = ~timestamps.isna()
    if not bool(valid_timestamp.all()):
        frame = frame.iloc[np.flatnonzero(valid_timestamp)].copy()
        timestamps = timestamps[valid_timestamp]
    if len(timestamps) == 0:
        raise DataSourceError("All input timestamps were missing or invalid.")

    if timestamps.tz is None:
        try:
            timestamps = timestamps.tz_localize(
                input_timezone, ambiguous="infer", nonexistent="shift_forward"
            )
        except Exception as exc:
            raise DataSourceError(
                f"Unable to localize naive timestamps to {input_timezone}: {exc}"
            ) from exc
    timestamps = timestamps.tz_convert(UTC)

    close_column = _find_column(frame, ["close", "Close", "c", "close_px"])
    if close_column is None:
        raise DataSourceError(
            "No close-price column was found. The script requires one-minute OHLCV bars."
        )

    result = pd.DataFrame(index=timestamps)
    result.index.name = "timestamp"
    result["close"] = pd.to_numeric(frame[close_column].to_numpy(), errors="coerce")

    volume_column = _find_column(frame, ["volume", "Volume", "vol", "v"])
    if volume_column is not None:
        result["volume"] = pd.to_numeric(
            frame[volume_column].to_numpy(), errors="coerce"
        )

    return result


def parse_as_of(value: str | None) -> pd.Timestamp | None:
    if value is None:
        return None
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        raise InvalidArgumentError(
            "--as-of must include a timezone or UTC suffix, for example 2026-07-22T15:30:00Z."
        )
    return timestamp.tz_convert(UTC)


def _iso(timestamp: pd.Timestamp | None) -> str | None:
    if timestamp is None:
        return None
    return timestamp.tz_convert(UTC).isoformat().replace("+00:00", "Z")


def _get_calendar(name: str):
    if xcals is None:
        raise InvalidArgumentError(
            "exchange-calendars is not installed. Install requirements.txt."
        )
    try:
        return xcals.get_calendar(name)
    except Exception as exc:
        raise InvalidArgumentError(
            f"Unknown or unavailable exchange calendar: {name}", calendar=name
        ) from exc


def clean_and_sessionize_bars(
    frame: pd.DataFrame,
    *,
    config: AnalysisConfig,
    as_of: pd.Timestamp,
) -> tuple[pd.DataFrame, pd.DataFrame, DataQuality]:
    """Validate bars, assign exchange sessions, and calculate contiguous returns."""
    quality = DataQuality(rows_loaded=len(frame))
    if frame.empty:
        raise InsufficientDataError("No bars are available after loading.")

    bars = frame.sort_index().copy()
    duplicate_mask = bars.index.duplicated(keep="last")
    quality.duplicate_timestamps_removed = int(duplicate_mask.sum())
    bars = bars.loc[~duplicate_mask]

    invalid_close = ~np.isfinite(bars["close"].to_numpy(dtype=float)) | (
        bars["close"].to_numpy(dtype=float) <= 0.0
    )
    quality.invalid_close_rows_removed = int(invalid_close.sum())
    bars = bars.loc[~invalid_close].copy()
    if bars.empty:
        raise InsufficientDataError("All bars had invalid close prices.")

    after_as_of = bars.index >= as_of
    quality.rows_after_as_of_removed = int(after_as_of.sum())
    bars = bars.loc[~after_as_of].copy()

    completed_cutoff = as_of - pd.Timedelta(seconds=config.completion_lag_seconds)
    incomplete = bars.index + BAR_DURATION > completed_cutoff
    quality.incomplete_bars_removed = int(incomplete.sum())
    bars = bars.loc[~incomplete].copy()
    if bars.empty:
        raise InsufficientDataError(
            "No fully closed bars are available at the requested as-of time."
        )

    calendar = _get_calendar(config.calendar_name)
    as_of_minute = as_of.floor("min")
    if config.mode is RunMode.LIVE:
        try:
            market_open = bool(
                calendar.is_open_on_minute(as_of_minute, ignore_breaks=True)
            )
        except Exception as exc:
            raise MarketClosedError(
                "The requested live time is outside the calendar's supported range.",
                as_of_utc=_iso(as_of),
            ) from exc
        if not market_open:
            raise MarketClosedError(
                f"{config.calendar_name} is not open at {_iso(as_of)}.",
                as_of_utc=_iso(as_of),
                calendar=config.calendar_name,
            )
        current_session_timestamp = calendar.minute_to_session(
            as_of_minute, direction="none"
        )
        quality.current_session = str(current_session_timestamp.date())

    min_day = (bars.index.min() - pd.Timedelta(days=4)).date()
    max_day = (max(bars.index.max(), as_of) + pd.Timedelta(days=4)).date()
    try:
        sessions = calendar.sessions_in_range(min_day, max_day)
    except Exception as exc:
        raise InvalidArgumentError(
            "The bar timestamps are outside the exchange calendar's supported range.",
            minimum_timestamp=_iso(pd.Timestamp(bars.index.min())),
            maximum_timestamp=_iso(pd.Timestamp(bars.index.max())),
        ) from exc

    session_labels = np.full(len(bars), None, dtype=object)
    minute_of_session = np.full(len(bars), -1, dtype=np.int32)
    index = bars.index

    for session in sessions:
        session_open = calendar.session_open(session)
        session_close = calendar.session_close(session)
        mask = (index >= session_open) & (index < session_close)
        if not mask.any():
            continue
        positions = np.flatnonzero(mask)
        session_labels[positions] = str(session.date())
        minute_of_session[positions] = (
            (index[positions] - session_open) / BAR_DURATION
        ).astype(int)

    outside = pd.isna(session_labels)
    quality.outside_regular_session_removed = int(outside.sum())
    bars = bars.loc[~outside].copy()
    bars["session"] = session_labels[~outside]
    bars["minute_of_session"] = minute_of_session[~outside]
    quality.valid_regular_session_bars = len(bars)

    if len(bars) < 3:
        raise InsufficientDataError(
            "Fewer than three valid regular-session bars remain after validation."
        )

    last_start = pd.Timestamp(bars.index.max())
    last_end = last_start + BAR_DURATION
    quality.last_bar_start_utc = _iso(last_start)
    quality.last_bar_end_utc = _iso(last_end)
    quality.last_bar_age_seconds = max(
        0.0, float((as_of - last_end).total_seconds())
    )

    if config.mode is RunMode.LIVE:
        last_session = str(bars["session"].iloc[-1])
        if last_session != quality.current_session:
            raise StaleDataError(
                "The newest closed bar is not from the current exchange session.",
                last_bar_session=last_session,
                current_session=quality.current_session,
                last_bar_end_utc=quality.last_bar_end_utc,
            )
        if quality.last_bar_age_seconds > config.stale_after_minutes * 60.0:
            raise StaleDataError(
                "The newest closed bar is too old for a just-in-time decision.",
                last_bar_age_seconds=quality.last_bar_age_seconds,
                stale_after_seconds=config.stale_after_minutes * 60.0,
                last_bar_end_utc=quality.last_bar_end_utc,
            )

    same_session = bars["session"].to_numpy()[1:] == bars[
        "session"
    ].to_numpy()[:-1]
    index_deltas = bars.index[1:] - bars.index[:-1]
    contiguous = index_deltas == BAR_DURATION
    quality.within_session_gaps = int(np.sum(same_session & ~contiguous))

    log_prices = np.log(bars["close"].to_numpy(dtype=float))
    returns = np.diff(log_prices)
    valid_return = same_session & contiguous & np.isfinite(returns)

    return_frame = bars.iloc[1:].copy()
    return_frame["log_return"] = returns
    return_frame = return_frame.loc[valid_return].copy()
    quality.valid_return_rows = len(return_frame)

    if return_frame.empty:
        raise InsufficientDataError(
            "No contiguous within-session one-minute returns could be calculated."
        )

    return bars, return_frame, quality


def select_matched_window(
    returns: pd.DataFrame, config: AnalysisConfig
) -> WindowData:
    """Select the live rolling window and same-time windows from prior sessions."""
    ordered_sessions = list(dict.fromkeys(returns["session"].astype(str).tolist()))
    if len(ordered_sessions) < 2:
        raise InsufficientDataError(
            "At least two distinct trading sessions are required; the 80/20 fallback was removed."
        )

    live_session = ordered_sessions[-1]
    live_session_rows = returns.loc[returns["session"] == live_session].copy()
    if live_session_rows.empty:
        raise InsufficientDataError("The latest session contains no valid returns.")

    end_minute = int(live_session_rows["minute_of_session"].max())
    start_minute = max(1, end_minute - config.window_minutes + 1)
    expected = end_minute - start_minute + 1

    live = live_session_rows.loc[
        live_session_rows["minute_of_session"].between(start_minute, end_minute)
    ].copy()
    live_coverage = len(live) / expected if expected else 0.0
    if live_coverage < config.min_session_coverage:
        raise InsufficientDataError(
            "The live window has insufficient contiguous return coverage.",
            live_session=live_session,
            expected_returns=expected,
            observed_returns=len(live),
            coverage=live_coverage,
            minimum_coverage=config.min_session_coverage,
        )

    prior_sessions = ordered_sessions[:-1]
    baseline_by_session: dict[str, pd.DataFrame] = {}
    rejected: list[str] = []
    for session in reversed(prior_sessions):
        candidate = returns.loc[
            (returns["session"] == session)
            & returns["minute_of_session"].between(start_minute, end_minute)
        ].copy()
        coverage = len(candidate) / expected if expected else 0.0
        if coverage >= config.min_session_coverage:
            baseline_by_session[session] = candidate
            if len(baseline_by_session) >= config.baseline_sessions:
                break
        else:
            rejected.append(session)

    # Restore chronological order after scanning newest-to-oldest.
    baseline_by_session = dict(reversed(list(baseline_by_session.items())))
    used_sessions = list(baseline_by_session)

    if len(used_sessions) < config.min_baseline_sessions:
        raise InsufficientDataError(
            "Too few matched historical sessions passed the coverage requirement.",
            matched_sessions=len(used_sessions),
            minimum_sessions=config.min_baseline_sessions,
            available_prior_sessions=len(prior_sessions),
            rejected_sessions=rejected,
            start_minute_of_session=start_minute,
            end_minute_of_session=end_minute,
        )

    baseline = pd.concat(baseline_by_session.values()).sort_index()
    return WindowData(
        live=live,
        baseline=baseline,
        baseline_by_session=baseline_by_session,
        live_session=live_session,
        start_minute_of_session=start_minute,
        end_minute_of_session=end_minute,
        expected_returns_per_session=expected,
        baseline_sessions_available=len(prior_sessions),
        baseline_sessions_used=used_sessions,
        rejected_baseline_sessions=rejected,
        live_coverage=live_coverage,
    )


def robust_location_scale(values: np.ndarray) -> tuple[float, float]:
    finite = np.asarray(values, dtype=float)
    finite = finite[np.isfinite(finite)]
    if finite.size == 0:
        raise InsufficientDataError("Cannot normalize an empty return sample.")

    center = float(np.median(finite))
    mad_scale = float(1.4826 * np.median(np.abs(finite - center)))
    if mad_scale > 1e-12:
        return center, mad_scale

    q25, q75 = np.quantile(finite, [0.25, 0.75])
    iqr_scale = float((q75 - q25) / 1.349)
    if iqr_scale > 1e-12:
        return center, iqr_scale

    standard_scale = float(np.std(finite, ddof=1)) if finite.size > 1 else 0.0
    if standard_scale > 1e-12:
        return center, standard_scale

    raise InsufficientDataError(
        "Baseline returns have effectively zero variation; transition states cannot be fitted."
    )


class IntradayVolatilityNormalizer:
    def __init__(
        self,
        *,
        bucket_minutes: int,
        min_bucket_samples: int,
        z_clip: float,
        enabled: bool,
    ) -> None:
        self.bucket_minutes = bucket_minutes
        self.min_bucket_samples = min_bucket_samples
        self.z_clip = z_clip
        self.enabled = enabled
        self.global_center = 0.0
        self.global_scale = 1.0
        self.bucket_parameters: dict[int, tuple[float, float, int]] = {}
        self.fallback_buckets: set[int] = set()

    def fit(self, baseline: pd.DataFrame) -> None:
        values = baseline["log_return"].to_numpy(dtype=float)
        self.global_center, self.global_scale = robust_location_scale(values)
        if not self.enabled:
            return

        buckets = (
            baseline["minute_of_session"].to_numpy(dtype=int)
            // self.bucket_minutes
        )
        for bucket in np.unique(buckets):
            bucket_values = values[buckets == bucket]
            if len(bucket_values) < self.min_bucket_samples:
                self.fallback_buckets.add(int(bucket))
                continue
            try:
                center, scale = robust_location_scale(bucket_values)
            except InsufficientDataError:
                self.fallback_buckets.add(int(bucket))
                continue
            self.bucket_parameters[int(bucket)] = (
                float(center),
                float(scale),
                int(len(bucket_values)),
            )

    def transform(self, frame: pd.DataFrame) -> np.ndarray:
        values = frame["log_return"].to_numpy(dtype=float)
        if not self.enabled:
            transformed = (values - self.global_center) / self.global_scale
            return np.clip(transformed, -self.z_clip, self.z_clip)

        buckets = (
            frame["minute_of_session"].to_numpy(dtype=int)
            // self.bucket_minutes
        )
        transformed = np.empty_like(values, dtype=float)
        for position, (value, bucket) in enumerate(zip(values, buckets)):
            parameters = self.bucket_parameters.get(int(bucket))
            if parameters is None:
                center, scale = self.global_center, self.global_scale
                self.fallback_buckets.add(int(bucket))
            else:
                center, scale, _ = parameters
            transformed[position] = (value - center) / scale
        return np.clip(transformed, -self.z_clip, self.z_clip)

    def describe(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "bucket_minutes": self.bucket_minutes,
            "minimum_bucket_samples": self.min_bucket_samples,
            "fitted_bucket_count": len(self.bucket_parameters),
            "fallback_buckets": sorted(self.fallback_buckets),
            "global_center_log_return": self.global_center,
            "global_scale_log_return": self.global_scale,
            "z_clip": self.z_clip,
        }


@dataclass
class StateDiscretizer:
    requested_states: int
    min_state_samples: int
    effective_states: int = 0
    internal_boundaries: np.ndarray = field(
        default_factory=lambda: np.array([], dtype=float)
    )
    baseline_state_counts: np.ndarray = field(
        default_factory=lambda: np.array([], dtype=int)
    )

    def fit(self, values: np.ndarray) -> None:
        finite = np.asarray(values, dtype=float)
        finite = finite[np.isfinite(finite)]
        if len(finite) < 3 * self.min_state_samples:
            raise InsufficientDataError(
                "Too few baseline returns are available to fit stable states.",
                baseline_returns=len(finite),
                minimum_required=3 * self.min_state_samples,
            )

        for states in range(self.requested_states, 2, -1):
            quantile_levels = np.arange(1, states, dtype=float) / states
            internal = np.quantile(finite, quantile_levels, method="linear")
            if np.any(np.diff(internal) <= 1e-12):
                continue
            assignments = np.searchsorted(internal, finite, side="right") + 1
            counts = np.bincount(assignments, minlength=states + 1)[1:]
            if len(counts) != states or int(counts.min()) < self.min_state_samples:
                continue
            self.effective_states = states
            self.internal_boundaries = internal.astype(float)
            self.baseline_state_counts = counts.astype(int)
            return

        raise InsufficientDataError(
            "Quantile boundaries collapsed because the return sample lacks enough distinct values."
        )

    def transform(self, values: np.ndarray) -> np.ndarray:
        if self.effective_states < 3:
            raise RuntimeError("StateDiscretizer must be fitted before transform().")
        assignments = (
            np.searchsorted(self.internal_boundaries, values, side="right") + 1
        )
        return np.clip(assignments, 1, self.effective_states).astype(np.int16)

    def labels(self) -> list[str]:
        return [
            f"S{index + 1} ({'lowest' if index == 0 else 'highest' if index == self.effective_states - 1 else 'ranked'} return)"
            for index in range(self.effective_states)
        ]


def estimate_transition_stats(frame: pd.DataFrame, states: np.ndarray, k: int) -> TransitionStats:
    if len(frame) != len(states):
        raise ValueError("Frame and state arrays must have the same length.")
    if len(states) < 2:
        return TransitionStats(
            counts=np.zeros((k, k), dtype=np.int64),
            origin_counts=np.zeros(k, dtype=np.int64),
            origins=np.array([], dtype=np.int16),
            destinations=np.array([], dtype=np.int16),
            valid_transition_count=0,
        )

    sessions = frame["session"].astype(str).to_numpy()
    same_session = sessions[1:] == sessions[:-1]
    contiguous = (frame.index[1:] - frame.index[:-1]) == BAR_DURATION
    valid = same_session & contiguous

    origins = states[:-1][valid].astype(np.int64) - 1
    destinations = states[1:][valid].astype(np.int64) - 1
    counts = np.zeros((k, k), dtype=np.int64)
    if origins.size:
        np.add.at(counts, (origins, destinations), 1)
    origin_counts = np.bincount(origins, minlength=k).astype(np.int64)

    return TransitionStats(
        counts=counts,
        origin_counts=origin_counts,
        origins=origins.astype(np.int16),
        destinations=destinations.astype(np.int16),
        valid_transition_count=int(origins.size),
    )


def estimate_baseline_matrix(counts: np.ndarray, alpha: float) -> np.ndarray:
    k = counts.shape[0]
    numerator = counts.astype(float) + alpha
    denominator = counts.sum(axis=1, keepdims=True).astype(float) + k * alpha
    return numerator / denominator


def estimate_live_matrix(
    counts: np.ndarray, baseline_matrix: np.ndarray, prior_strength: float
) -> np.ndarray:
    numerator = counts.astype(float) + prior_strength * baseline_matrix
    denominator = counts.sum(axis=1, keepdims=True).astype(float) + prior_strength
    return numerator / denominator


def estimate_occupancy(
    origin_counts: np.ndarray,
    *,
    alpha: float | None = None,
    baseline_occupancy: np.ndarray | None = None,
    prior_strength: float | None = None,
) -> np.ndarray:
    counts = origin_counts.astype(float)
    if baseline_occupancy is not None:
        if prior_strength is None or prior_strength <= 0.0:
            raise ValueError("A positive prior_strength is required.")
        adjusted = counts + prior_strength * baseline_occupancy
    else:
        if alpha is None or alpha <= 0.0:
            raise ValueError("A positive alpha is required.")
        adjusted = counts + alpha
    return adjusted / adjusted.sum()


def estimate_joint_distribution(
    counts: np.ndarray,
    *,
    alpha: float | None = None,
    baseline_joint: np.ndarray | None = None,
    prior_strength: float | None = None,
) -> np.ndarray:
    flat = counts.astype(float).ravel()
    if baseline_joint is not None:
        if prior_strength is None or prior_strength <= 0.0:
            raise ValueError("A positive prior_strength is required.")
        adjusted = flat + prior_strength * baseline_joint
    else:
        if alpha is None or alpha <= 0.0:
            raise ValueError("A positive alpha is required.")
        adjusted = flat + alpha
    return adjusted / adjusted.sum()


def jensen_shannon_divergence(p: np.ndarray, q: np.ndarray) -> float:
    """Base-2 Jensen-Shannon divergence, bounded from 0 to 1."""
    p = np.asarray(p, dtype=float)
    q = np.asarray(q, dtype=float)
    if p.shape != q.shape:
        raise ValueError("JSD inputs must have identical shapes.")
    p = p / p.sum()
    q = q / q.sum()
    midpoint = 0.5 * (p + q)

    def kl(left: np.ndarray, right: np.ndarray) -> float:
        valid = (left > 0.0) & (right > 0.0)
        return float(np.sum(left[valid] * np.log2(left[valid] / right[valid])))

    value = 0.5 * kl(p, midpoint) + 0.5 * kl(q, midpoint)
    return float(np.clip(value, 0.0, 1.0))


def conditional_transition_jsd(
    baseline_matrix: np.ndarray,
    live_matrix: np.ndarray,
    baseline_occupancy: np.ndarray,
    live_occupancy: np.ndarray,
) -> tuple[float, np.ndarray]:
    row_values = np.array(
        [
            jensen_shannon_divergence(baseline_matrix[row], live_matrix[row])
            for row in range(baseline_matrix.shape[0])
        ]
    )
    symmetric_weights = 0.5 * (baseline_occupancy + live_occupancy)
    symmetric_weights /= symmetric_weights.sum()
    return float(np.dot(symmetric_weights, row_values)), row_values


def empirical_conditional_entropy(
    matrix: np.ndarray, occupancy: np.ndarray
) -> float:
    with np.errstate(divide="ignore", invalid="ignore"):
        log_matrix = np.where(matrix > 0.0, np.log2(matrix), 0.0)
    row_entropy = -np.sum(matrix * log_matrix, axis=1)
    return float(np.dot(occupancy, row_entropy))


def sequence_surprise_bits(
    baseline_matrix: np.ndarray, stats: TransitionStats
) -> float:
    if stats.valid_transition_count == 0:
        return math.nan
    probabilities = baseline_matrix[stats.origins, stats.destinations]
    probabilities = np.clip(probabilities, 1e-15, 1.0)
    return float(-np.mean(np.log2(probabilities)))


def calculate_metric_set(
    baseline_stats: TransitionStats,
    live_stats: TransitionStats,
    *,
    alpha: float,
    live_prior_strength: float,
) -> tuple[dict[str, float], dict[str, np.ndarray]]:
    k = baseline_stats.counts.shape[0]
    baseline_matrix = estimate_baseline_matrix(baseline_stats.counts, alpha)
    live_matrix = estimate_live_matrix(
        live_stats.counts, baseline_matrix, live_prior_strength
    )
    baseline_occupancy = estimate_occupancy(
        baseline_stats.origin_counts, alpha=alpha
    )
    live_occupancy = estimate_occupancy(
        live_stats.origin_counts,
        baseline_occupancy=baseline_occupancy,
        prior_strength=live_prior_strength,
    )
    baseline_joint = estimate_joint_distribution(
        baseline_stats.counts, alpha=alpha
    )
    joint_prior_strength = k * live_prior_strength
    live_joint = estimate_joint_distribution(
        live_stats.counts,
        baseline_joint=baseline_joint,
        prior_strength=joint_prior_strength,
    )
    conditional_jsd, row_jsd = conditional_transition_jsd(
        baseline_matrix, live_matrix, baseline_occupancy, live_occupancy
    )

    metrics = {
        "occupancy_jsd": jensen_shannon_divergence(
            baseline_occupancy, live_occupancy
        ),
        "conditional_transition_jsd": conditional_jsd,
        "joint_transition_jsd": jensen_shannon_divergence(
            baseline_joint, live_joint
        ),
        "sequence_surprise_bits_per_transition": sequence_surprise_bits(
            baseline_matrix, live_stats
        ),
        "baseline_empirical_conditional_entropy_bits": empirical_conditional_entropy(
            baseline_matrix, baseline_occupancy
        ),
        "live_empirical_conditional_entropy_bits": empirical_conditional_entropy(
            live_matrix, live_occupancy
        ),
    }
    arrays = {
        "baseline_matrix": baseline_matrix,
        "live_matrix": live_matrix,
        "baseline_occupancy": baseline_occupancy,
        "live_occupancy": live_occupancy,
        "baseline_joint": baseline_joint,
        "live_joint": live_joint,
        "row_jsd": row_jsd,
    }
    return metrics, arrays


def posterior_joint_jsd_interval(
    baseline_counts: np.ndarray,
    live_counts: np.ndarray,
    *,
    alpha: float,
    live_prior_strength: float,
    samples: int,
    random_seed: int,
) -> dict[str, float] | None:
    if samples <= 0:
        return None
    k = baseline_counts.shape[0]
    baseline_parameters = baseline_counts.astype(float).ravel() + alpha
    baseline_mean = baseline_parameters / baseline_parameters.sum()
    live_parameters = live_counts.astype(float).ravel() + (
        k * live_prior_strength * baseline_mean
    )

    rng = np.random.default_rng(random_seed)
    baseline_draws = rng.dirichlet(baseline_parameters, size=samples)
    live_draws = rng.dirichlet(live_parameters, size=samples)
    midpoint = 0.5 * (baseline_draws + live_draws)
    with np.errstate(divide="ignore", invalid="ignore"):
        baseline_terms = np.where(
            baseline_draws > 0.0,
            baseline_draws * np.log2(baseline_draws / midpoint),
            0.0,
        )
        live_terms = np.where(
            live_draws > 0.0,
            live_draws * np.log2(live_draws / midpoint),
            0.0,
        )
    draws = 0.5 * baseline_terms.sum(axis=1) + 0.5 * live_terms.sum(axis=1)
    lower, median, upper = np.quantile(draws, [0.025, 0.5, 0.975])
    return {
        "posterior_samples": int(samples),
        "median": float(median),
        "lower_95": float(lower),
        "upper_95": float(upper),
    }


def calibrate_from_baseline_sessions(
    baseline_frame: pd.DataFrame,
    states: np.ndarray,
    *,
    k: int,
    alpha: float,
    live_prior_strength: float,
    min_live_transitions: int,
) -> list[dict[str, float | str | int]]:
    """
    Approximate leave-one-session-out calibration.

    Transition counts for each held-out session are excluded from its comparison
    baseline. State boundaries and volatility normalization remain fixed from the
    full historical baseline, which keeps the live state definition stable.
    """
    frame = baseline_frame.copy()
    frame["state"] = states
    sessions = list(dict.fromkeys(frame["session"].astype(str).tolist()))
    results: list[dict[str, float | str | int]] = []

    for held_out in sessions:
        held_frame = frame.loc[frame["session"] == held_out].copy()
        train_frame = frame.loc[frame["session"] != held_out].copy()
        if train_frame.empty or held_frame.empty:
            continue
        train_stats = estimate_transition_stats(
            train_frame, train_frame["state"].to_numpy(dtype=np.int16), k
        )
        held_stats = estimate_transition_stats(
            held_frame, held_frame["state"].to_numpy(dtype=np.int16), k
        )
        if held_stats.valid_transition_count < min_live_transitions:
            continue
        if train_stats.valid_transition_count < max(min_live_transitions, k * 2):
            continue
        metrics, _ = calculate_metric_set(
            train_stats,
            held_stats,
            alpha=alpha,
            live_prior_strength=live_prior_strength,
        )
        results.append(
            {
                "session": held_out,
                "transitions": held_stats.valid_transition_count,
                "occupancy_jsd": metrics["occupancy_jsd"],
                "conditional_transition_jsd": metrics[
                    "conditional_transition_jsd"
                ],
                "joint_transition_jsd": metrics["joint_transition_jsd"],
                "sequence_surprise_bits_per_transition": metrics[
                    "sequence_surprise_bits_per_transition"
                ],
            }
        )
    return results


def empirical_percentile(value: float, sample: Sequence[float]) -> float | None:
    finite = np.asarray(sample, dtype=float)
    finite = finite[np.isfinite(finite)]
    if finite.size == 0 or not np.isfinite(value):
        return None
    # Add-one out-of-sample rank estimate prevents a misleading 0th percentile.
    rank = 1 + int(np.sum(finite <= value))
    return float(100.0 * rank / (finite.size + 1))


def summarize_calibration(
    live_metrics: dict[str, float],
    calibration_rows: list[dict[str, float | str | int]],
    config: AnalysisConfig,
) -> dict[str, Any]:
    metric_names = [
        "occupancy_jsd",
        "conditional_transition_jsd",
        "joint_transition_jsd",
        "sequence_surprise_bits_per_transition",
    ]
    percentiles: dict[str, float | None] = {}
    thresholds: dict[str, dict[str, float] | None] = {}
    for metric in metric_names:
        sample = [float(row[metric]) for row in calibration_rows]
        percentiles[metric] = empirical_percentile(live_metrics[metric], sample)
        if sample:
            thresholds[metric] = {
                "warning_value": float(
                    np.quantile(sample, config.warning_percentile / 100.0)
                ),
                "block_value": float(
                    np.quantile(sample, config.block_percentile / 100.0)
                ),
            }
        else:
            thresholds[metric] = None

    primary_percentiles = [
        percentiles["joint_transition_jsd"],
        percentiles["sequence_surprise_bits_per_transition"],
    ]
    available_primary = [value for value in primary_percentiles if value is not None]
    composite = max(available_primary) if available_primary else None
    sample_count = len(calibration_rows)
    return {
        "method": "approximate leave-one-session-out on matched historical windows",
        "sample_count": sample_count,
        "minimum_for_execution_policy": config.min_calibration_sessions,
        "quality": (
            "adequate"
            if sample_count >= config.min_calibration_sessions
            else "limited"
        ),
        "warning_percentile": config.warning_percentile,
        "block_percentile": config.block_percentile,
        "metric_percentiles": percentiles,
        "metric_thresholds": thresholds,
        "composite_anomaly_percentile": composite,
        "historical_samples": calibration_rows,
    }


def directional_metrics(
    baseline_frame: pd.DataFrame,
    baseline_states: np.ndarray,
    live_states: np.ndarray,
    arrays: dict[str, np.ndarray],
    *,
    tail_fraction: float,
    adverse_tail_shift: float,
) -> dict[str, Any]:
    k = arrays["baseline_matrix"].shape[0]
    state_medians = np.zeros(k, dtype=float)
    raw_returns = baseline_frame["log_return"].to_numpy(dtype=float)
    for state in range(1, k + 1):
        values = raw_returns[baseline_states == state]
        state_medians[state - 1] = float(np.median(values)) if len(values) else 0.0

    baseline_matrix = arrays["baseline_matrix"]
    live_matrix = arrays["live_matrix"]
    baseline_occupancy = arrays["baseline_occupancy"]
    live_occupancy = arrays["live_occupancy"]

    expected_baseline = float(
        baseline_occupancy @ baseline_matrix @ state_medians
    )
    expected_live = float(live_occupancy @ live_matrix @ state_medians)
    current_state = int(live_states[-1])
    expected_from_current = float(live_matrix[current_state - 1] @ state_medians)

    tail_count = max(1, int(math.ceil(k * tail_fraction)))
    tail_count = min(tail_count, max(1, k // 2))
    lower = slice(0, tail_count)
    upper = slice(k - tail_count, k)

    baseline_negative_tail = float(
        baseline_occupancy @ baseline_matrix[:, lower].sum(axis=1)
    )
    live_negative_tail = float(
        live_occupancy @ live_matrix[:, lower].sum(axis=1)
    )
    baseline_positive_tail = float(
        baseline_occupancy @ baseline_matrix[:, upper].sum(axis=1)
    )
    live_positive_tail = float(
        live_occupancy @ live_matrix[:, upper].sum(axis=1)
    )

    def persistence(
        occupancy: np.ndarray, matrix: np.ndarray, state_slice: slice
    ) -> float:
        indices = np.arange(k)[state_slice]
        weights = occupancy[indices]
        if weights.sum() <= 0.0:
            weights = np.ones(len(indices), dtype=float) / len(indices)
        else:
            weights = weights / weights.sum()
        return float(np.dot(weights, matrix[indices, state_slice].sum(axis=1)))

    baseline_negative_persistence = persistence(
        baseline_occupancy, baseline_matrix, lower
    )
    live_negative_persistence = persistence(live_occupancy, live_matrix, lower)
    baseline_positive_persistence = persistence(
        baseline_occupancy, baseline_matrix, upper
    )
    live_positive_persistence = persistence(live_occupancy, live_matrix, upper)

    negative_shift = live_negative_tail - baseline_negative_tail
    positive_shift = live_positive_tail - baseline_positive_tail
    downside_evidence = bool(
        negative_shift >= adverse_tail_shift and expected_live < expected_baseline
    )
    upside_evidence = bool(
        positive_shift >= adverse_tail_shift and expected_live > expected_baseline
    )
    if downside_evidence and upside_evidence:
        direction = "two-sided-volatility-expansion"
    elif downside_evidence:
        direction = "downside"
    elif upside_evidence:
        direction = "upside"
    else:
        direction = "neutral-or-mixed"

    return {
        "classification": direction,
        "downside_evidence": downside_evidence,
        "upside_evidence": upside_evidence,
        "current_state": current_state,
        "tail_state_count_per_side": tail_count,
        "state_median_log_returns": state_medians.tolist(),
        "baseline_expected_next_return_bps": expected_baseline * 10_000.0,
        "live_expected_next_return_bps": expected_live * 10_000.0,
        "expected_next_return_from_current_state_bps": expected_from_current
        * 10_000.0,
        "baseline_negative_tail_probability": baseline_negative_tail,
        "live_negative_tail_probability": live_negative_tail,
        "negative_tail_probability_shift": negative_shift,
        "baseline_positive_tail_probability": baseline_positive_tail,
        "live_positive_tail_probability": live_positive_tail,
        "positive_tail_probability_shift": positive_shift,
        "baseline_negative_tail_persistence": baseline_negative_persistence,
        "live_negative_tail_persistence": live_negative_persistence,
        "baseline_positive_tail_persistence": baseline_positive_persistence,
        "live_positive_tail_persistence": live_positive_persistence,
        "adverse_tail_shift_required": adverse_tail_shift,
    }


def top_joint_jsd_contributors(
    baseline_joint: np.ndarray,
    live_joint: np.ndarray,
    *,
    k: int,
    limit: int,
) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    midpoint = 0.5 * (baseline_joint + live_joint)
    with np.errstate(divide="ignore", invalid="ignore"):
        contributions = 0.5 * np.where(
            baseline_joint > 0.0,
            baseline_joint * np.log2(baseline_joint / midpoint),
            0.0,
        ) + 0.5 * np.where(
            live_joint > 0.0,
            live_joint * np.log2(live_joint / midpoint),
            0.0,
        )
    order = np.argsort(contributions)[::-1][:limit]
    results: list[dict[str, Any]] = []
    for flat_index in order:
        origin, destination = divmod(int(flat_index), k)
        results.append(
            {
                "origin_state": origin + 1,
                "destination_state": destination + 1,
                "baseline_joint_probability": float(baseline_joint[flat_index]),
                "live_joint_probability": float(live_joint[flat_index]),
                "probability_change": float(
                    live_joint[flat_index] - baseline_joint[flat_index]
                ),
                "jsd_contribution": float(contributions[flat_index]),
            }
        )
    return results


def make_decision(
    *,
    config: AnalysisConfig,
    calibration: dict[str, Any],
    direction: dict[str, Any],
) -> tuple[Decision, ExitCode, list[str]]:
    reasons: list[str] = []
    anomaly_percentile = calibration["composite_anomaly_percentile"]
    calibration_count = int(calibration["sample_count"])

    if anomaly_percentile is None:
        warning = True
        extreme = False
        reasons.append(
            "No empirical calibration percentile is available; human review is required."
        )
    else:
        warning = anomaly_percentile >= config.warning_percentile
        extreme = anomaly_percentile >= config.block_percentile
        reasons.append(
            f"Composite anomaly percentile is {anomaly_percentile:.2f}."
        )

    calibration_adequate = calibration_count >= config.min_calibration_sessions
    if not calibration_adequate:
        warning = True
        extreme = False
        reasons.append(
            f"Only {calibration_count} calibration sessions were available; "
            f"at least {config.min_calibration_sessions} are required for an automatic block."
        )

    if config.action.is_risk_reducing:
        if warning:
            reasons.append(
                "The requested action reduces risk, so anomaly uncertainty does not block execution."
            )
            return Decision.ALLOW_RISK_REDUCTION, ExitCode.OK, reasons
        reasons.append("No material anomaly warning was detected.")
        return Decision.PASS, ExitCode.OK, reasons

    adverse = False
    if config.action.is_long_risk_increasing:
        adverse = bool(direction["downside_evidence"])
        if adverse:
            reasons.append(
                "Downside-tail probability and expected-return evidence are adverse to a long entry."
            )
    elif config.action.is_short_risk_increasing:
        adverse = bool(direction["upside_evidence"])
        if adverse:
            reasons.append(
                "Upside-tail probability and expected-return evidence are adverse to a short entry."
            )

    if extreme and adverse and calibration_adequate:
        reasons.append(
            "An extreme calibrated anomaly and action-specific adverse direction are both present."
        )
        return Decision.BLOCK_NEW_RISK, ExitCode.BLOCK_NEW_RISK, reasons

    if warning:
        if extreme and not adverse:
            reasons.append(
                "The regime anomaly is extreme, but it is not directionally adverse enough for an automatic block."
            )
        else:
            reasons.append(
                "The regime is unusual relative to matched history; human review is required."
            )
        return Decision.CAUTION, ExitCode.CAUTION, reasons

    reasons.append("The matched-window diagnostics are within the calibrated range.")
    return Decision.PASS, ExitCode.OK, reasons


def analyze_market_frame(
    raw_frame: pd.DataFrame,
    *,
    source_metadata: dict[str, Any],
    config: AnalysisConfig,
    as_of: pd.Timestamp | None = None,
    timestamp_column: str | None = None,
    symbol_column: str | None = None,
    input_timezone: str = "America/New_York",
) -> dict[str, Any]:
    """Public, testable entry point for a loaded market-data frame."""
    config.validate()
    ticker = config.ticker.upper()
    canonical = canonicalize_market_frame(
        raw_frame,
        ticker=ticker,
        timestamp_column=timestamp_column,
        symbol_column=symbol_column,
        input_timezone=input_timezone,
    )

    if as_of is None:
        if config.mode is RunMode.LIVE:
            as_of = pd.Timestamp.now(tz=UTC)
        else:
            if canonical.empty:
                raise InsufficientDataError("Cannot infer replay as-of from empty data.")
            as_of = pd.Timestamp(canonical.index.max()) + BAR_DURATION + pd.Timedelta(
                seconds=config.completion_lag_seconds
            )
    elif as_of.tzinfo is None:
        raise InvalidArgumentError("The as-of timestamp must be timezone-aware.")
    else:
        as_of = as_of.tz_convert(UTC)

    bars, returns, data_quality = clean_and_sessionize_bars(
        canonical, config=config, as_of=as_of
    )
    window = select_matched_window(returns, config)

    normalizer = IntradayVolatilityNormalizer(
        bucket_minutes=config.volatility_bucket_minutes,
        min_bucket_samples=config.min_bucket_samples,
        z_clip=config.z_clip,
        enabled=config.volatility_normalization,
    )
    normalizer.fit(window.baseline)
    normalized_baseline = normalizer.transform(window.baseline)
    normalized_live = normalizer.transform(window.live)

    discretizer = StateDiscretizer(
        requested_states=config.requested_states,
        min_state_samples=config.min_state_samples,
    )
    discretizer.fit(normalized_baseline)
    k = discretizer.effective_states
    baseline_states = discretizer.transform(normalized_baseline)
    live_states = discretizer.transform(normalized_live)

    baseline_stats = estimate_transition_stats(window.baseline, baseline_states, k)
    live_stats = estimate_transition_stats(window.live, live_states, k)
    if live_stats.valid_transition_count < config.min_live_transitions:
        raise InsufficientDataError(
            "The live window contains too few valid one-minute state transitions.",
            live_transitions=live_stats.valid_transition_count,
            minimum_live_transitions=config.min_live_transitions,
        )
    minimum_baseline_transitions = max(
        config.min_live_transitions * config.min_baseline_sessions, k * k
    )
    if baseline_stats.valid_transition_count < minimum_baseline_transitions:
        raise InsufficientDataError(
            "The matched baseline contains too few transitions for the effective state count.",
            baseline_transitions=baseline_stats.valid_transition_count,
            minimum_baseline_transitions=minimum_baseline_transitions,
            effective_states=k,
        )

    metrics, arrays = calculate_metric_set(
        baseline_stats,
        live_stats,
        alpha=config.alpha,
        live_prior_strength=config.live_prior_strength,
    )
    posterior_interval = posterior_joint_jsd_interval(
        baseline_stats.counts,
        live_stats.counts,
        alpha=config.alpha,
        live_prior_strength=config.live_prior_strength,
        samples=config.posterior_samples,
        random_seed=config.random_seed,
    )

    calibration_rows = calibrate_from_baseline_sessions(
        window.baseline,
        baseline_states,
        k=k,
        alpha=config.alpha,
        live_prior_strength=config.live_prior_strength,
        min_live_transitions=config.min_live_transitions,
    )
    calibration = summarize_calibration(metrics, calibration_rows, config)
    direction = directional_metrics(
        window.baseline,
        baseline_states,
        live_states,
        arrays,
        tail_fraction=config.tail_fraction,
        adverse_tail_shift=config.adverse_tail_shift,
    )
    decision, exit_code, reasons = make_decision(
        config=config, calibration=calibration, direction=direction
    )

    analysis_time = pd.Timestamp.now(tz=UTC)
    valid_until = (
        analysis_time + pd.Timedelta(seconds=config.result_ttl_seconds)
        if config.mode is RunMode.LIVE
        else as_of + pd.Timedelta(seconds=config.result_ttl_seconds)
    )
    top_contributors = top_joint_jsd_contributors(
        arrays["baseline_joint"],
        arrays["live_joint"],
        k=k,
        limit=config.top_transitions,
    )

    warnings: list[str] = []
    if k < config.requested_states:
        warnings.append(
            f"The state count was reduced from {config.requested_states} to {k} "
            "because higher-resolution quantile boundaries were not stable."
        )
    if calibration["quality"] == "limited":
        warnings.append(
            "Historical calibration is limited; the script will not issue an automatic block."
        )
    if source_metadata.get("provider") == "yfinance":
        warnings.append(
            "Yahoo Finance is retained as a prototype source; production decisions should use a controlled market-data feed."
        )
    if normalizer.fallback_buckets:
        warnings.append(
            "Some intraday volatility buckets used the global robust scale because bucket support was insufficient."
        )

    result = {
        "schema_version": SCHEMA_VERSION,
        "script_version": SCRIPT_VERSION,
        "analysis_id": str(uuid.uuid4()),
        "status": "ok",
        "ticker": ticker,
        "action": config.action.value,
        "mode": config.mode.value,
        "decision": decision.value,
        "exit_code": int(exit_code),
        "analysis_time_utc": _iso(analysis_time),
        "as_of_utc": _iso(as_of),
        "valid_until_utc": _iso(valid_until),
        "source": source_metadata,
        "calendar": config.calendar_name,
        "data_quality": asdict(data_quality),
        "window": {
            "live_session": window.live_session,
            "window_minutes_requested": config.window_minutes,
            "start_minute_of_session": window.start_minute_of_session,
            "end_minute_of_session": window.end_minute_of_session,
            "expected_returns_per_session": window.expected_returns_per_session,
            "live_return_rows": len(window.live),
            "live_coverage": window.live_coverage,
            "baseline_sessions_available": window.baseline_sessions_available,
            "baseline_sessions_used": window.baseline_sessions_used,
            "rejected_baseline_sessions": window.rejected_baseline_sessions,
            "baseline_return_rows": len(window.baseline),
            "baseline_transitions": baseline_stats.valid_transition_count,
            "live_transitions": live_stats.valid_transition_count,
        },
        "normalization": normalizer.describe(),
        "state_model": {
            "requested_states": config.requested_states,
            "effective_states": k,
            "labels": discretizer.labels(),
            "internal_boundaries_normalized_return": discretizer.internal_boundaries.tolist(),
            "baseline_state_counts": discretizer.baseline_state_counts.tolist(),
            "smoothing_alpha": config.alpha,
            "live_baseline_prior_strength_per_row": config.live_prior_strength,
        },
        "metrics": {
            **metrics,
            "joint_transition_jsd_posterior_interval": posterior_interval,
            "conditional_jsd_by_origin_state": arrays["row_jsd"].tolist(),
            "baseline_state_occupancy": arrays["baseline_occupancy"].tolist(),
            "live_state_occupancy": arrays["live_occupancy"].tolist(),
            "baseline_self_transition_probability": np.diag(
                arrays["baseline_matrix"]
            ).tolist(),
            "live_self_transition_probability": np.diag(
                arrays["live_matrix"]
            ).tolist(),
        },
        "calibration": calibration,
        "direction": direction,
        "top_transition_contributors": top_contributors,
        "policy": {
            "warning_percentile": config.warning_percentile,
            "block_percentile": config.block_percentile,
            "automatic_block_requires": [
                "adequate matched-session calibration",
                "extreme composite anomaly percentile",
                "directional evidence adverse to the proposed risk-increasing order",
            ],
            "risk_reducing_orders_are_not_blocked_by_model_uncertainty": True,
        },
        "reasons": reasons,
        "warnings": warnings,
    }
    return result


def error_result(
    error: AnalysisError,
    *,
    ticker: str | None,
    action: str | None,
    mode: str | None,
) -> dict[str, Any]:
    now = pd.Timestamp.now(tz=UTC)
    return {
        "schema_version": SCHEMA_VERSION,
        "script_version": SCRIPT_VERSION,
        "analysis_id": str(uuid.uuid4()),
        "status": "error",
        "ticker": ticker.upper() if ticker else None,
        "action": action,
        "mode": mode,
        "decision": error.decision.value,
        "exit_code": int(error.exit_code),
        "analysis_time_utc": _iso(now),
        "message": str(error),
        "details": error.details,
    }


def format_number(value: Any, digits: int = 4) -> str:
    if value is None:
        return "n/a"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not np.isfinite(number):
        return "n/a"
    return f"{number:.{digits}f}"


def render_text(result: dict[str, Any]) -> str:
    if result.get("status") != "ok":
        lines = [
            "=" * 76,
            "JIT TRANSITION-MATRIX DIAGNOSTIC - ERROR",
            "=" * 76,
            f"Ticker:    {result.get('ticker') or 'n/a'}",
            f"Decision:  {result.get('decision')}",
            f"Exit code: {result.get('exit_code')}",
            f"Message:   {result.get('message')}",
        ]
        details = result.get("details") or {}
        if details:
            lines.append("Details:")
            for key, value in details.items():
                lines.append(f"  {key}: {value}")
        return "\n".join(lines)

    metrics = result["metrics"]
    calibration = result["calibration"]
    direction = result["direction"]
    window = result["window"]
    data_quality = result["data_quality"]
    state_model = result["state_model"]
    posterior = metrics.get("joint_transition_jsd_posterior_interval")

    lines = [
        "=" * 76,
        f"JIT TRANSITION-MATRIX DIAGNOSTIC v{result['script_version']}",
        "=" * 76,
        f"Ticker:       {result['ticker']}",
        f"Action:       {result['action']}",
        f"Mode:         {result['mode']}",
        f"As of:        {result['as_of_utc']}",
        f"Last bar end: {data_quality['last_bar_end_utc']}",
        f"Valid until:  {result['valid_until_utc']}",
        f"Source:       {result['source'].get('provider')}",
        "",
        "Matched window",
        "--------------",
        f"Live session:             {window['live_session']}",
        f"Minute-of-session range:   {window['start_minute_of_session']} to {window['end_minute_of_session']}",
        f"Baseline sessions used:    {len(window['baseline_sessions_used'])}",
        f"Baseline/live transitions: {window['baseline_transitions']} / {window['live_transitions']}",
        f"Within-session data gaps:  {data_quality['within_session_gaps']}",
        "",
        "Model",
        "-----",
        f"States requested/effective: {state_model['requested_states']} / {state_model['effective_states']}",
        f"Intraday normalization:      {result['normalization']['enabled']}",
        f"Live prior strength/row:     {state_model['live_baseline_prior_strength_per_row']}",
        "",
        "Diagnostics",
        "-----------",
        f"State-occupancy JSD:         {format_number(metrics['occupancy_jsd'], 6)}",
        f"Conditional-transition JSD:  {format_number(metrics['conditional_transition_jsd'], 6)}",
        f"Joint-transition JSD:        {format_number(metrics['joint_transition_jsd'], 6)}",
        f"Sequence surprise:           {format_number(metrics['sequence_surprise_bits_per_transition'], 4)} bits/transition",
        f"Baseline conditional entropy:{format_number(metrics['baseline_empirical_conditional_entropy_bits'], 4)} bits/transition",
        f"Live conditional entropy:    {format_number(metrics['live_empirical_conditional_entropy_bits'], 4)} bits/transition",
    ]
    if posterior:
        lines.append(
            "Joint JSD posterior 95%:    "
            f"{format_number(posterior['lower_95'], 6)} to "
            f"{format_number(posterior['upper_95'], 6)} "
            f"(median {format_number(posterior['median'], 6)})"
        )

    lines.extend(
        [
            "",
            "Historical calibration",
            "----------------------",
            f"Method:                     {calibration['method']}",
            f"Calibration samples:        {calibration['sample_count']} ({calibration['quality']})",
            f"Composite anomaly percentile:{format_number(calibration['composite_anomaly_percentile'], 2)}",
            "",
            "Directional context",
            "-------------------",
            f"Classification:             {direction['classification']}",
            f"Negative-tail shift:        {format_number(direction['negative_tail_probability_shift'], 4)}",
            f"Positive-tail shift:        {format_number(direction['positive_tail_probability_shift'], 4)}",
            f"Live expected next return:  {format_number(direction['live_expected_next_return_bps'], 3)} bps",
            f"Current-state expected:     {format_number(direction['expected_next_return_from_current_state_bps'], 3)} bps",
        ]
    )

    contributors = result.get("top_transition_contributors") or []
    if contributors:
        lines.extend(["", "Largest joint-transition JSD contributors", "-----------------------------------------"])
        for item in contributors:
            lines.append(
                f"S{item['origin_state']} -> S{item['destination_state']}: "
                f"baseline {item['baseline_joint_probability']:.4f}, "
                f"live {item['live_joint_probability']:.4f}, "
                f"change {item['probability_change']:+.4f}"
            )

    lines.extend(["", "Decision", "--------"])
    lines.append(f"{result['decision']} (exit code {result['exit_code']})")
    for reason in result.get("reasons", []):
        lines.append(f"- {reason}")
    for warning in result.get("warnings", []):
        lines.append(f"WARNING: {warning}")
    lines.append("=" * 76)
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Matched-window one-minute transition-matrix regime diagnostic with "
            "structured execution-policy output."
        )
    )
    parser.add_argument("--ticker", required=True, help="Ticker symbol, e.g. MCD")
    parser.add_argument(
        "--source",
        choices=["auto", "yfinance", "file", "dbn"],
        default="auto",
        help="Market-data source. auto selects dbn/file when --input is supplied.",
    )
    parser.add_argument("--input", type=Path, help="Local CSV/JSON/Parquet/DBN path")
    parser.add_argument(
        "--period",
        default="5d",
        help="yfinance period for one-minute data (default: 5d)",
    )
    parser.add_argument(
        "--mode", choices=[mode.value for mode in RunMode], default="live"
    )
    parser.add_argument(
        "--as-of",
        help="Timezone-aware analysis time. Required for precise replay; inferred otherwise.",
    )
    parser.add_argument(
        "--action",
        choices=[action.value for action in Action],
        default=Action.DIAGNOSTIC.value,
        help="Order context used by the decision policy.",
    )
    parser.add_argument("--calendar", default="XNYS")
    parser.add_argument("--timestamp-column")
    parser.add_argument("--symbol-column")
    parser.add_argument("--input-timezone", default="America/New_York")
    parser.add_argument("--states", type=int, default=8)
    parser.add_argument("--window-minutes", type=int, default=60)
    parser.add_argument("--baseline-sessions", type=int, default=20)
    parser.add_argument("--min-baseline-sessions", type=int, default=4)
    parser.add_argument("--min-live-transitions", type=int, default=20)
    parser.add_argument("--min-session-coverage", type=float, default=0.80)
    parser.add_argument("--alpha", type=float, default=0.5)
    parser.add_argument("--prior-strength", type=float, default=5.0)
    parser.add_argument(
        "--disable-volatility-normalization", action="store_true"
    )
    parser.add_argument("--volatility-bucket-minutes", type=int, default=15)
    parser.add_argument("--min-bucket-samples", type=int, default=8)
    parser.add_argument("--z-clip", type=float, default=12.0)
    parser.add_argument("--min-state-samples", type=int, default=5)
    parser.add_argument("--tail-fraction", type=float, default=0.25)
    parser.add_argument("--adverse-tail-shift", type=float, default=0.05)
    parser.add_argument("--warning-percentile", type=float, default=95.0)
    parser.add_argument("--block-percentile", type=float, default=99.0)
    parser.add_argument("--min-calibration-sessions", type=int, default=4)
    parser.add_argument("--posterior-samples", type=int, default=500)
    parser.add_argument("--random-seed", type=int, default=1729)
    parser.add_argument("--stale-after-minutes", type=float, default=3.0)
    parser.add_argument("--completion-lag-seconds", type=float, default=2.0)
    parser.add_argument("--ttl-seconds", type=int, default=90)
    parser.add_argument("--top-transitions", type=int, default=5)
    parser.add_argument("--format", choices=["text", "json"], default="text")
    parser.add_argument("--output", type=Path, help="Also write the result to this path")
    parser.add_argument(
        "--compact-json", action="store_true", help="Emit JSON on one line"
    )
    parser.add_argument(
        "--traceback", action="store_true", help="Print unexpected tracebacks to stderr"
    )
    return parser


def resolve_provider(args: argparse.Namespace) -> MarketDataProvider:
    source = args.source
    if source == "auto":
        if args.input is None:
            source = "yfinance"
        else:
            lower_name = args.input.name.lower()
            source = "dbn" if lower_name.endswith((".dbn", ".dbn.zst")) else "file"

    if source == "yfinance":
        if args.input is not None:
            raise InvalidArgumentError(
                "--input cannot be combined with --source yfinance."
            )
        return YFinanceProvider(period=args.period)

    if args.input is None:
        raise InvalidArgumentError(f"--input is required for --source {source}.")
    if source == "dbn":
        return DatabentoDbnProvider(args.input)
    return LocalFileProvider(
        args.input,
        timestamp_column=args.timestamp_column,
        symbol_column=args.symbol_column,
        input_timezone=args.input_timezone,
    )


def result_to_output(result: dict[str, Any], args: argparse.Namespace) -> str:
    if args.format == "json":
        return json.dumps(
            result,
            indent=None if args.compact_json else 2,
            separators=(",", ":") if args.compact_json else None,
            sort_keys=False,
            allow_nan=False,
        )
    return render_text(result)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    ticker = args.ticker.upper() if getattr(args, "ticker", None) else None

    try:
        config = AnalysisConfig(
            ticker=args.ticker,
            action=Action(args.action),
            mode=RunMode(args.mode),
            calendar_name=args.calendar,
            requested_states=args.states,
            window_minutes=args.window_minutes,
            baseline_sessions=args.baseline_sessions,
            min_baseline_sessions=args.min_baseline_sessions,
            min_live_transitions=args.min_live_transitions,
            min_session_coverage=args.min_session_coverage,
            alpha=args.alpha,
            live_prior_strength=args.prior_strength,
            volatility_normalization=not args.disable_volatility_normalization,
            volatility_bucket_minutes=args.volatility_bucket_minutes,
            min_bucket_samples=args.min_bucket_samples,
            z_clip=args.z_clip,
            min_state_samples=args.min_state_samples,
            tail_fraction=args.tail_fraction,
            adverse_tail_shift=args.adverse_tail_shift,
            warning_percentile=args.warning_percentile,
            block_percentile=args.block_percentile,
            min_calibration_sessions=args.min_calibration_sessions,
            posterior_samples=args.posterior_samples,
            random_seed=args.random_seed,
            stale_after_minutes=args.stale_after_minutes,
            completion_lag_seconds=args.completion_lag_seconds,
            result_ttl_seconds=args.ttl_seconds,
            top_transitions=args.top_transitions,
        )
        config.validate()
        provider = resolve_provider(args)
        raw_frame, source_metadata = provider.load(config.ticker.upper())
        as_of = parse_as_of(args.as_of)
        result = analyze_market_frame(
            raw_frame,
            source_metadata=source_metadata,
            config=config,
            as_of=as_of,
            timestamp_column=args.timestamp_column,
            symbol_column=args.symbol_column,
            input_timezone=args.input_timezone,
        )
        exit_code = int(result["exit_code"])
    except AnalysisError as exc:
        result = error_result(
            exc,
            ticker=ticker,
            action=getattr(args, "action", None),
            mode=getattr(args, "mode", None),
        )
        exit_code = int(exc.exit_code)
    except Exception as exc:  # last-resort structured failure
        if getattr(args, "traceback", False):
            traceback.print_exc(file=sys.stderr)
        internal = AnalysisError(
            f"Unexpected internal error: {exc}",
            decision=Decision.INTERNAL_ERROR,
            exit_code=ExitCode.INTERNAL_ERROR,
        )
        result = error_result(
            internal,
            ticker=ticker,
            action=getattr(args, "action", None),
            mode=getattr(args, "mode", None),
        )
        exit_code = int(ExitCode.INTERNAL_ERROR)

    output = result_to_output(result, args)
    print(output)
    if args.output:
        try:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(output + "\n", encoding="utf-8")
        except OSError as exc:
            print(f"Unable to write --output file: {exc}", file=sys.stderr)
            return int(ExitCode.DATA_SOURCE_ERROR)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
