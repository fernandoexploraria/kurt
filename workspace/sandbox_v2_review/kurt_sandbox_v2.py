#!/usr/bin/env python3
"""Point-in-time-aware, bar-by-bar replay backtester.

This module is a hardened revision of the original ``kurt_sandbox.py``.  It
preserves the original strategy's broad intent—discounted limit entries,
ATR-based sizing, trailing stops, take-profit targets, optional Quiver/DEA
adjustments, local artifacts, and optional Google Sheets publishing—while
removing several sources of look-ahead bias and operational risk.

Important modeling rule
-----------------------
A bar does not reveal the order in which its high and low occurred.  The
``--intrabar-policy`` option therefore controls ambiguous bars.  The default,
``conservative``, resolves a bar that touches both stop and target against the
position and never awards an intrabar take-profit after a non-opening entry
unless the selected path policy supports it.

The program does not place brokerage orders.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import quote


VERSION = "2.0.0"

DEFAULT_SANDBOX_DIR = Path("/root/.openclaw/workspace/memory/sandbox")
DEFAULT_RAPIDAPI_HOST = "tradingview-data1.p.rapidapi.com"

DEFAULT_SHIELD_FILE = Path("/root/.openclaw/workspace/memory/quiver_shield.json")
DEFAULT_CACHE_FILE = Path("/root/.openclaw/workspace/memory/exchange_cache.json")
DEFAULT_OPTIMIZED_FILE = Path("/root/.openclaw/workspace/memory/optimized_entries.json")
DEFAULT_DEA_SCORES_FILE = Path("/root/.openclaw/workspace/memory/dea_scores.json")

BETA_THRESHOLD = 1.05
DEFAULT_ATR_MULTIPLIER = 3.0
LOW_BETA_MULTIPLIER = 4.0
LOSER_LEASH_MIN_PCT = 0.05
LOSER_LEASH_MAX_PCT = 0.08
LOSER_LEASH_ATR_FACTOR = 1.5
DEFAULT_RISK_FREE_RATE = 0.045

LOGGER = logging.getLogger("kurt_sandbox")
UTC = timezone.utc


class ReplayError(RuntimeError):
    """Base exception for expected replay failures."""


class ConfigurationError(ReplayError):
    """Raised when command-line or environment configuration is invalid."""


class DataSourceError(ReplayError):
    """Raised when market data cannot be retrieved or normalized."""


class PublishError(ReplayError):
    """Raised when an explicitly requested Google Sheets publication fails."""


@dataclass(frozen=True, slots=True)
class Candle:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None


@dataclass(slots=True)
class Position:
    trade_id: str
    ticker: str
    shares: int
    entry_time: datetime
    entry_price: float
    entry_gross: float
    entry_fee: float
    entry_slippage: float
    entry_outlay: float
    entry_atr: float
    trailing_multiplier: float
    highest_seen: float
    stop_price: float
    target_price: float
    beta: float

    def to_json(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["entry_time"] = self.entry_time.isoformat()
        return payload


@dataclass(frozen=True, slots=True)
class ExitEvent:
    reason: str
    raw_price: float
    ambiguous: bool = False


@dataclass(slots=True)
class MarketSeries:
    ticker: str
    symbol: str
    source: str
    candles: list[Candle]
    sim_indices: list[int]
    index_by_time: dict[datetime, int]
    invalid_rows: int = 0
    duplicate_rows: int = 0

    @property
    def first_sim_timestamp(self) -> datetime:
        return self.candles[self.sim_indices[0]].timestamp

    @property
    def last_sim_timestamp(self) -> datetime:
        return self.candles[self.sim_indices[-1]].timestamp


@dataclass(frozen=True, slots=True)
class EntryCandidate:
    ticker: str
    candle_index: int
    raw_entry_price: float
    entry_limit: float
    previous_atr: float
    beta: float
    trailing_multiplier: float
    target_price: float
    initial_stop: float
    dea_multiplier: float
    catalyst_multiplier: float
    regime_multiplier: float
    factor_adjustment: float
    point_in_time_record_used: bool


@dataclass(slots=True)
class ReplayArtifacts:
    run_id: str
    summary: dict[str, Any]
    positions: dict[str, Position]
    transactions: list[dict[str, Any]]
    completed_trades: list[dict[str, Any]]
    equity_curve: list[dict[str, Any]]
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Generic utilities
# ---------------------------------------------------------------------------


def configure_logging(level: str) -> None:
    numeric_level = getattr(logging, level.upper(), None)
    if not isinstance(numeric_level, int):
        raise ConfigurationError(f"Invalid log level: {level}")
    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def utc_now() -> datetime:
    return datetime.now(tz=UTC)


def is_finite_positive(value: float) -> bool:
    return math.isfinite(value) and value > 0.0


def round_price(value: float, decimals: int) -> float:
    return round(float(value), decimals)


def parse_date_start(value: str) -> datetime:
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise ConfigurationError(
            f"Invalid date '{value}'. Expected YYYY-MM-DD."
        ) from exc
    return parsed.replace(tzinfo=UTC)


def parse_bool(value: Any) -> bool:
    """Parse common command-line boolean spellings, including legacy values."""
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    raise argparse.ArgumentTypeError(
        f"Expected a boolean value, received {value!r}"
    )


def parse_intrabar_policy(value: str) -> str:
    """Accept descriptive policies and legacy stop/target-first spellings."""
    normalized = str(value).strip().lower().replace("_", "-")
    aliases = {
        "conservative": "conservative",
        "stop-first": "conservative",
        "optimistic": "optimistic",
        "target-first": "optimistic",
        "ohlc": "ohlc",
        "olhc": "olhc",
    }
    try:
        return aliases[normalized]
    except KeyError as exc:
        raise argparse.ArgumentTypeError(
            "Expected conservative, optimistic, ohlc, olhc, stop_first, or target_first"
        ) from exc


def parse_factor_mode(value: str) -> str:
    """Normalize friendly factor-mode aliases to the internal values."""
    normalized = str(value).strip().lower().replace("_", "-")
    aliases = {
        "off": "off",
        "neutral": "off",
        "none": "off",
        "point-in-time": "point-in-time",
        "snapshots": "point-in-time",
        "snapshot": "point-in-time",
        "static": "static",
    }
    try:
        return aliases[normalized]
    except KeyError as exc:
        raise argparse.ArgumentTypeError(
            "Expected off/neutral, point-in-time/snapshots, or static"
        ) from exc


def parse_timestamp(value: Any) -> datetime:
    """Parse epoch or common ISO/date strings and normalize them to UTC."""
    if value is None:
        raise ValueError("timestamp is missing")
    if isinstance(value, bool):
        raise ValueError("boolean is not a timestamp")

    if isinstance(value, (int, float)):
        numeric = float(value)
        if not math.isfinite(numeric):
            raise ValueError("timestamp is not finite")
        magnitude = abs(numeric)
        if magnitude >= 1e17:  # nanoseconds
            numeric /= 1_000_000_000.0
        elif magnitude >= 1e14:  # microseconds
            numeric /= 1_000_000.0
        elif magnitude >= 1e11:  # milliseconds
            numeric /= 1_000.0
        return datetime.fromtimestamp(numeric, tz=UTC)

    text = str(value).strip()
    if not text:
        raise ValueError("timestamp is empty")

    try:
        numeric = float(text)
    except ValueError:
        numeric = None
    if numeric is not None and math.isfinite(numeric):
        return parse_timestamp(numeric)

    normalized = text
    if normalized.upper().endswith(" UTC"):
        normalized = normalized[:-4] + "+00:00"
    elif normalized.endswith(("Z", "z")):
        normalized = normalized[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        parsed = None

    if parsed is None:
        for pattern in (
            "%Y/%m/%d",
            "%m/%d/%Y",
            "%Y-%m-%d %H:%M:%S",
            "%Y/%m/%d %H:%M:%S",
        ):
            try:
                parsed = datetime.strptime(text, pattern)
                break
            except ValueError:
                continue
    if parsed is None:
        raise ValueError(f"Unsupported timestamp value: {value!r}")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def load_json(path: Path, *, required: bool = False) -> Any:
    if not path.exists():
        if required:
            raise ConfigurationError(f"Required JSON file does not exist: {path}")
        LOGGER.warning("Optional JSON file not found: %s", path)
        return {}

    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        if required:
            raise ConfigurationError(f"Could not load JSON file {path}: {exc}") from exc
        LOGGER.warning("Ignoring unreadable optional JSON file %s: %s", path, exc)
        return {}


def normalize_ticker_cache(cache: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize ticker-like top-level keys while preserving non-string keys."""
    return {
        (key.upper() if isinstance(key, str) else str(key)): value
        for key, value in cache.items()
    }


FACTOR_SNAPSHOT_FILE_NAMES = {
    "quiver_shield": "shield",
    "optimized_entries": "optimized",
    "dea_scores": "dea",
}


def _merge_snapshot_payload(
    destination: dict[str, Any], payload: Mapping[str, Any], as_of: str
) -> int:
    merged = 0
    for ticker, record in payload.items():
        if not isinstance(ticker, str) or not isinstance(record, Mapping):
            continue
        current = destination.get(ticker.upper())
        if not isinstance(current, Mapping):
            current = {}
        current_copy = dict(current)
        snapshots = current_copy.get("snapshots")
        snapshot_copy = dict(snapshots) if isinstance(snapshots, Mapping) else {}
        snapshot_copy[as_of] = dict(record)
        current_copy["snapshots"] = snapshot_copy
        destination[ticker.upper()] = current_copy
        merged += 1
    return merged


def load_factor_snapshot_directory(
    directory: Path,
) -> tuple[dict[str, dict[str, Any]], int]:
    """Load dated point-in-time factor files from two documented layouts.

    Supported examples::

        snapshots/quiver_shield_2026-01-15.json
        snapshots/2026-01-15/quiver_shield.json
    """
    if not directory.is_dir():
        raise ConfigurationError(
            f"--factor-snapshot-dir is not a directory: {directory}"
        )

    caches: dict[str, dict[str, Any]] = {
        "shield": {},
        "optimized": {},
        "dea": {},
    }
    loaded_records = 0
    files: list[tuple[Path, str, str]] = []

    date_pattern = r"(\d{4}-\d{2}-\d{2})"
    for path in sorted(directory.glob("*.json")):
        match = re.fullmatch(
            rf"(quiver_shield|optimized_entries|dea_scores)[_-]{date_pattern}\.json",
            path.name,
            flags=re.IGNORECASE,
        )
        if match:
            files.append((path, match.group(1).lower(), match.group(2)))

    for date_dir in sorted(directory.iterdir()):
        if not date_dir.is_dir() or not re.fullmatch(date_pattern, date_dir.name):
            continue
        for base_name in FACTOR_SNAPSHOT_FILE_NAMES:
            path = date_dir / f"{base_name}.json"
            if path.exists():
                files.append((path, base_name, date_dir.name))

    if not files:
        raise ConfigurationError(
            f"No recognized factor snapshot JSON files were found in {directory}"
        )

    for path, base_name, as_of in files:
        try:
            datetime.strptime(as_of, "%Y-%m-%d")
        except ValueError as exc:
            raise ConfigurationError(
                f"Invalid snapshot date in {path.name}: {as_of}"
            ) from exc
        payload = load_json(path, required=True)
        if not isinstance(payload, Mapping):
            raise ConfigurationError(
                f"Factor snapshot must be a ticker-keyed JSON object: {path}"
            )
        kind = FACTOR_SNAPSHOT_FILE_NAMES[base_name]
        loaded_records += _merge_snapshot_payload(caches[kind], payload, as_of)

    return caches, loaded_records


def merge_factor_cache(
    base: Mapping[str, Any], snapshot_cache: Mapping[str, Any]
) -> dict[str, Any]:
    merged = {str(key).upper(): value for key, value in base.items()}
    for ticker, snapshot_record in snapshot_cache.items():
        current = merged.get(ticker)
        current_copy = dict(current) if isinstance(current, Mapping) else {}
        incoming = (
            snapshot_record.get("snapshots", {})
            if isinstance(snapshot_record, Mapping)
            else {}
        )
        existing = current_copy.get("snapshots", {})
        combined = dict(existing) if isinstance(existing, Mapping) else {}
        if isinstance(incoming, Mapping):
            combined.update(incoming)
        current_copy["snapshots"] = combined
        merged[ticker] = current_copy
    return merged


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def save_json_atomic(data: Any, path: Path) -> None:
    """Write JSON atomically so a crash cannot leave a partial state file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = handle.name
            json.dump(data, handle, indent=2, sort_keys=True, default=_json_default)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, 0o644)
        os.replace(temp_path, path)
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


def save_csv_atomic(
    rows: Sequence[Mapping[str, Any]], path: Path, fieldnames: Sequence[str]
) -> None:
    """Write a deterministic CSV atomically using only the standard library."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = handle.name
            writer = csv.DictWriter(
                handle, fieldnames=list(fieldnames), extrasaction="ignore"
            )
            writer.writeheader()
            for row in rows:
                writer.writerow(
                    {
                        name: "" if row.get(name) is None else row.get(name)
                        for name in fieldnames
                    }
                )
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, 0o644)
        os.replace(temp_path, path)
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


# ---------------------------------------------------------------------------
# Market-data acquisition and normalization
# ---------------------------------------------------------------------------


def _extract_history(payload: Any) -> list[Mapping[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, Mapping)]

    if not isinstance(payload, Mapping):
        raise DataSourceError("Market-data response is neither a list nor an object")

    data = payload.get("data")
    if isinstance(data, Mapping) and isinstance(data.get("history"), list):
        return [row for row in data["history"] if isinstance(row, Mapping)]
    if isinstance(payload.get("history"), list):
        return [row for row in payload["history"] if isinstance(row, Mapping)]

    message = payload.get("message") or payload.get("error") or "history array missing"
    raise DataSourceError(f"Market-data response did not contain candle history: {message}")


def fetch_historical_prices(
    symbol: str,
    timeframe: str,
    range_count: int,
    api_key: str,
    *,
    host: str,
    timeout_seconds: float,
    retries: int,
) -> list[Mapping[str, Any]]:
    """Fetch historical candles with status checks, retries, and bounded backoff."""
    encoded_symbol = quote(symbol, safe="")
    url = f"https://{host}/api/price/{encoded_symbol}"
    headers = {
        "x-rapidapi-key": api_key,
        "x-rapidapi-host": host,
        "accept": "application/json",
    }
    params = {"timeframe": timeframe, "range": str(range_count)}

    try:
        import requests
    except ImportError as exc:
        raise ConfigurationError(
            "RapidAPI mode requires the optional 'requests' package. "
            "Install requirements_kurt_sandbox_v2.txt or use --data-dir."
        ) from exc

    last_error: Exception | None = None
    with requests.Session() as session:
        for attempt in range(retries + 1):
            try:
                response = session.get(
                    url,
                    headers=headers,
                    params=params,
                    timeout=timeout_seconds,
                )
                response.raise_for_status()
                payload = response.json()
                return _extract_history(payload)
            except (requests.RequestException, ValueError, DataSourceError) as exc:
                last_error = exc
                if attempt >= retries:
                    break
                delay = min(8.0, 0.75 * (2**attempt))
                LOGGER.warning(
                    "Market-data request failed for %s (attempt %d/%d): %s; retrying in %.2fs",
                    symbol,
                    attempt + 1,
                    retries + 1,
                    exc,
                    delay,
                )
                time.sleep(delay)

    raise DataSourceError(f"Failed to fetch historical prices for {symbol}: {last_error}")


def _read_local_market_file(path: Path) -> list[Mapping[str, Any]]:
    try:
        if path.suffix.lower() == ".csv":
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                return [dict(row) for row in csv.DictReader(handle)]
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return _extract_history(payload)
    except (OSError, ValueError, json.JSONDecodeError, DataSourceError) as exc:
        raise DataSourceError(f"Could not load local market data from {path}: {exc}") from exc


def find_local_market_file(data_dir: Path, ticker: str, symbol: str) -> Path | None:
    names = [
        ticker,
        ticker.upper(),
        symbol,
        symbol.replace(":", "_"),
        symbol.replace(":", "-"),
    ]
    extensions = [".json", ".csv"]
    for name in names:
        for extension in extensions:
            candidate = data_dir / f"{name}{extension}"
            if candidate.exists():
                return candidate
    return None


def _coerce_float(row: Mapping[str, Any], *keys: str) -> float:
    for key in keys:
        if key in row and row[key] is not None:
            value = float(row[key])
            if not math.isfinite(value):
                raise ValueError(f"{key} is not finite")
            return value
    raise ValueError(f"missing one of fields: {', '.join(keys)}")


def normalize_candles(
    rows: Iterable[Mapping[str, Any]],
) -> tuple[list[Candle], int, int]:
    """Validate, normalize, sort, and de-duplicate raw candles."""
    by_timestamp: dict[datetime, Candle] = {}
    invalid_rows = 0
    duplicate_rows = 0

    for row in rows:
        try:
            timestamp = parse_timestamp(
                row.get("time", row.get("date", row.get("timestamp")))
            )
            open_price = _coerce_float(row, "open", "o")
            high_price = _coerce_float(row, "max", "high", "h")
            low_price = _coerce_float(row, "min", "low", "l")
            close_price = _coerce_float(row, "close", "c")
            volume_value = row.get("volume", row.get("v"))
            volume = None if volume_value is None else float(volume_value)

            if not all(
                is_finite_positive(price)
                for price in (open_price, high_price, low_price, close_price)
            ):
                raise ValueError("OHLC prices must be finite and positive")
            if high_price < max(open_price, close_price, low_price):
                raise ValueError("high is below another OHLC value")
            if low_price > min(open_price, close_price, high_price):
                raise ValueError("low is above another OHLC value")
            if volume is not None and (not math.isfinite(volume) or volume < 0):
                raise ValueError("volume must be finite and non-negative")

            candle = Candle(
                timestamp=timestamp,
                open=open_price,
                high=high_price,
                low=low_price,
                close=close_price,
                volume=volume,
            )
            if timestamp in by_timestamp:
                duplicate_rows += 1
            by_timestamp[timestamp] = candle
        except (TypeError, ValueError, OverflowError, OSError):
            invalid_rows += 1

    candles = [by_timestamp[key] for key in sorted(by_timestamp)]
    if not candles:
        raise DataSourceError("No valid candles remained after normalization")
    return candles, invalid_rows, duplicate_rows


def timeframe_bars_per_day(timeframe: str) -> float:
    text = timeframe.strip().upper()
    if text in {"D", "1D", "DAY", "DAILY"}:
        return 1.0
    if text in {"W", "1W", "WEEK", "WEEKLY"}:
        return 1.0 / 5.0
    if text in {"H", "1H", "60M"}:
        return 6.5
    if text.endswith("H") and text[:-1].isdigit():
        hours = int(text[:-1])
        return max(1.0, 6.5 / hours)
    if text.endswith("M") and text[:-1].isdigit():
        minutes = int(text[:-1])
        return max(1.0, 390.0 / minutes)
    if text.isdigit():
        minutes = int(text)
        if minutes > 0:
            return max(1.0, 390.0 / minutes)
    return 1.0


def estimate_range_count(days: int, timeframe: str, atr_period: int) -> int:
    bars_per_day = timeframe_bars_per_day(timeframe)
    simulation_bars = math.ceil(days * bars_per_day)
    warmup_bars = max(atr_period + 5, math.ceil(10 * bars_per_day))
    return max(60, simulation_bars + warmup_bars + math.ceil(simulation_bars * 0.25))


def resolve_exchange_prefix(exchange_cache: Mapping[str, Any], ticker: str) -> str:
    raw = exchange_cache.get(ticker)
    if isinstance(raw, str) and raw.strip():
        prefix = raw.strip()
    elif isinstance(raw, Mapping):
        prefix = str(raw.get("prefix") or raw.get("exchange") or "NASDAQ:").strip()
    else:
        prefix = "NASDAQ:"
    if not prefix.endswith(":"):
        prefix += ":"
    return prefix


def build_market_series(
    ticker: str,
    *,
    exchange_cache: Mapping[str, Any],
    data_dir: Path | None,
    api_key: str | None,
    rapidapi_host: str,
    timeframe: str,
    range_count: int,
    start: datetime,
    end_exclusive: datetime,
    atr_period: int,
    timeout_seconds: float,
    retries: int,
) -> MarketSeries:
    prefix = resolve_exchange_prefix(exchange_cache, ticker)
    symbol = f"{prefix}{ticker}"

    local_file = find_local_market_file(data_dir, ticker, symbol) if data_dir else None
    if local_file:
        LOGGER.info("Loading %s candles from %s", ticker, local_file)
        raw_rows = _read_local_market_file(local_file)
        source = f"local:{local_file}"
    else:
        if data_dir:
            raise DataSourceError(
                f"No local market-data file found for {ticker} in {data_dir}"
            )
        if not api_key:
            raise ConfigurationError(
                "RAPIDAPI_KEY is required unless --data-dir supplies local candles"
            )
        LOGGER.info("Fetching historical candles for %s", symbol)
        raw_rows = fetch_historical_prices(
            symbol,
            timeframe,
            range_count,
            api_key,
            host=rapidapi_host,
            timeout_seconds=timeout_seconds,
            retries=retries,
        )
        source = f"rapidapi:{rapidapi_host}"

    candles, invalid_rows, duplicate_rows = normalize_candles(raw_rows)
    sim_indices = [
        idx
        for idx, candle in enumerate(candles)
        if start <= candle.timestamp < end_exclusive
    ]
    if not sim_indices:
        returned_start = candles[0].timestamp.isoformat()
        returned_end = candles[-1].timestamp.isoformat()
        raise DataSourceError(
            f"No {ticker} candles fall within {start.isoformat()} to "
            f"{end_exclusive.isoformat()} (exclusive). Returned coverage is "
            f"{returned_start} to {returned_end}. The range-based API may not "
            "cover an older requested period; use --data-dir for reproducible archives."
        )

    first_sim_index = sim_indices[0]
    if first_sim_index < atr_period:
        raise DataSourceError(
            f"{ticker} has only {first_sim_index} warm-up bars before the replay; "
            f"at least {atr_period} are required for ATR({atr_period})."
        )

    return MarketSeries(
        ticker=ticker,
        symbol=symbol,
        source=source,
        candles=candles,
        sim_indices=sim_indices,
        index_by_time={candles[index].timestamp: index for index in sim_indices},
        invalid_rows=invalid_rows,
        duplicate_rows=duplicate_rows,
    )


# ---------------------------------------------------------------------------
# Point-in-time auxiliary data
# ---------------------------------------------------------------------------


def _try_parse_asof(value: Any) -> datetime | None:
    try:
        return parse_timestamp(value)
    except (ValueError, TypeError, OverflowError):
        return None


def _strip_temporal_metadata(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in record.items()
        if key not in {"as_of", "date", "timestamp", "effective_date"}
    }


def _dated_records(raw: Any) -> list[tuple[datetime, dict[str, Any]]]:
    candidates: list[tuple[datetime, dict[str, Any]]] = []

    def add_record(record: Any, explicit_date: Any = None) -> None:
        if not isinstance(record, Mapping):
            return
        date_value = explicit_date
        if date_value is None:
            for key in ("as_of", "effective_date", "date", "timestamp"):
                if key in record:
                    date_value = record[key]
                    break
        as_of = _try_parse_asof(date_value)
        if as_of is not None:
            candidates.append((as_of, _strip_temporal_metadata(record)))

    if isinstance(raw, list):
        for item in raw:
            add_record(item)
        return candidates

    if not isinstance(raw, Mapping):
        return candidates

    add_record(raw)

    for container_key in ("history", "snapshots", "records", "values"):
        container = raw.get(container_key)
        if isinstance(container, list):
            for item in container:
                add_record(item)
        elif isinstance(container, Mapping):
            for date_key, record in container.items():
                add_record(record, date_key)

    # Also support a dictionary whose top-level keys are dates.
    for date_key, record in raw.items():
        if date_key in {"history", "snapshots", "records", "values"}:
            continue
        if _try_parse_asof(date_key) is not None:
            add_record(record, date_key)

    return candidates


def count_undated_factor_records(
    caches: Sequence[Mapping[str, Any]], tickers: Sequence[str]
) -> int:
    """Count non-empty ticker records that have no usable as-of timestamp."""
    count = 0
    for cache in caches:
        for ticker in tickers:
            raw = cache.get(ticker)
            if raw and not _dated_records(raw):
                count += 1
    return count


def resolve_factor_record(
    cache: Mapping[str, Any],
    ticker: str,
    as_of: datetime,
    mode: str,
) -> tuple[dict[str, Any], bool]:
    """Resolve a factor record without using future observations.

    Returns ``(record, temporal_record_used)``.  In ``static`` mode, a plain
    undated record is returned and the boolean is False, making the potential
    look-ahead condition explicit in the summary.  In ``point-in-time`` mode,
    undated records are ignored.
    """
    if mode == "off":
        return {}, False

    raw = cache.get(ticker, {})
    dated = sorted(_dated_records(raw), key=lambda item: item[0])
    eligible = [record for timestamp, record in dated if timestamp <= as_of]
    if eligible:
        return eligible[-1], True

    if mode == "static" and isinstance(raw, Mapping):
        return dict(raw), False
    return {}, False


def get_signal_adjustment(
    shield_record: Mapping[str, Any], dpi_bearish: bool
) -> float:
    """Return a bounded +/-15% adjustment to distance, not absolute price."""
    adjustment = 0.0

    try:
        latest_dpi = float(shield_record.get("dpi", 0.5))
    except (TypeError, ValueError):
        latest_dpi = 0.5
    latest_dpi = max(0.0, min(1.0, latest_dpi))
    if latest_dpi > 0.50:
        shift = min((latest_dpi - 0.50) * 0.2, 0.05)
        adjustment += -shift if dpi_bearish else shift

    try:
        score = float(shield_record.get("score", 50.0))
    except (TypeError, ValueError):
        score = 50.0
    score = max(0.0, min(100.0, score))
    adjustment += (score - 50.0) * 0.002

    return max(-0.15, min(0.15, adjustment))


def _safe_float(record: Mapping[str, Any], key: str, default: float) -> float:
    try:
        value = float(record.get(key, default))
        return value if math.isfinite(value) else default
    except (TypeError, ValueError):
        return default


def compute_dea_size_multiplier(
    ticker: str,
    dea_cache: Mapping[str, Any],
    tickers: Sequence[str],
    as_of: datetime,
    factor_mode: str,
) -> tuple[float, bool]:
    records: dict[str, tuple[dict[str, Any], bool]] = {
        symbol: resolve_factor_record(dea_cache, symbol, as_of, factor_mode)
        for symbol in tickers
    }
    ticker_record, temporal_used = records.get(ticker, ({}, False))
    if not ticker_record:
        return 1.0, temporal_used

    cohort_scores: list[float] = []
    for record, _ in records.values():
        if "dea_score" not in record:
            continue
        score = _safe_float(record, "dea_score", math.nan)
        if math.isfinite(score):
            cohort_scores.append(score * 100.0)

    if len(cohort_scores) < 4:
        return 1.0, temporal_used
    cohort_scores.sort()
    if cohort_scores[-1] - cohort_scores[0] < 1.0:
        return 1.0, temporal_used

    score = _safe_float(ticker_record, "dea_score", 0.0) * 100.0
    percentile = sum(1 for value in cohort_scores if value <= score) / len(cohort_scores)
    if percentile >= 0.5:
        multiplier = 1.0 + ((percentile - 0.5) / 0.5) * 0.25
    else:
        multiplier = 0.5 + (percentile / 0.5) * 0.5
    return round(max(0.5, min(1.25, multiplier)), 3), temporal_used


# ---------------------------------------------------------------------------
# Strategy and execution model
# ---------------------------------------------------------------------------


def calculate_atr(candles: Sequence[Candle], end_index: int, period: int) -> float | None:
    """Return a simple ATR ending at ``end_index`` using only known bars."""
    if period <= 0 or end_index < period:
        return None
    start_index = end_index - period + 1
    true_ranges: list[float] = []
    for index in range(start_index, end_index + 1):
        current = candles[index]
        previous_close = candles[index - 1].close
        true_ranges.append(
            max(
                current.high - current.low,
                abs(current.high - previous_close),
                abs(current.low - previous_close),
            )
        )
    if not true_ranges:
        return None
    atr = sum(true_ranges) / len(true_ranges)
    return atr if is_finite_positive(atr) else None


def select_trailing_multiplier(
    beta: float,
    optimized_record: Mapping[str, Any],
    default_multiplier: float,
    forced_multiplier: float | None,
) -> float:
    if forced_multiplier is not None:
        return forced_multiplier
    if beta >= BETA_THRESHOLD:
        return max(
            0.01,
            _safe_float(optimized_record, "exit_multiplier_used", default_multiplier),
        )
    return LOW_BETA_MULTIPLIER


def resolve_existing_exit(
    candle: Candle,
    stop_price: float,
    target_price: float,
    intrabar_policy: str,
) -> ExitEvent | None:
    """Resolve stop/target events for a position active at bar open."""
    if candle.open <= stop_price:
        return ExitEvent("STOP_GAP", candle.open)
    if candle.open >= target_price:
        return ExitEvent("TAKE_PROFIT_GAP", candle.open)

    stop_touched = candle.low <= stop_price
    target_touched = candle.high >= target_price

    if stop_touched and target_touched:
        if intrabar_policy in {"conservative", "olhc"}:
            return ExitEvent("STOP_AMBIGUOUS", stop_price, ambiguous=True)
        return ExitEvent("TAKE_PROFIT_AMBIGUOUS", target_price, ambiguous=True)
    if stop_touched:
        return ExitEvent("STOP", stop_price)
    if target_touched:
        return ExitEvent("TAKE_PROFIT", target_price)
    return None


def resolve_entry_bar_exit(
    candle: Candle,
    *,
    entry_limit: float,
    stop_price: float,
    target_price: float,
    intrabar_policy: str,
) -> ExitEvent | None:
    """Resolve same-bar events after a new limit entry.

    If the entry was not filled at the open, the pre-entry path is unknown.
    The conservative policy therefore recognizes a same-bar stop but does not
    award a same-bar target.  Explicit OHLC/OLHC policies use the chosen path.
    """
    entered_at_open = candle.open <= entry_limit
    if entered_at_open:
        return resolve_existing_exit(candle, stop_price, target_price, intrabar_policy)

    stop_touched = candle.low <= stop_price
    target_touched = candle.high >= target_price

    if intrabar_policy == "conservative":
        if stop_touched:
            return ExitEvent("ENTRY_BAR_STOP", stop_price, ambiguous=target_touched)
        return None
    if intrabar_policy == "optimistic":
        if target_touched:
            return ExitEvent(
                "ENTRY_BAR_TAKE_PROFIT", target_price, ambiguous=stop_touched
            )
        if stop_touched:
            return ExitEvent("ENTRY_BAR_STOP", stop_price)
        return None
    if intrabar_policy == "ohlc":
        # Open -> High -> Low -> Close: the high occurs before a limit buy on
        # the low leg, so only a subsequent stop can be inferred.
        if stop_touched:
            return ExitEvent("ENTRY_BAR_STOP", stop_price, ambiguous=target_touched)
        return None
    if intrabar_policy == "olhc":
        # Open -> Low -> High -> Close: a stop on the low leg precedes target.
        if stop_touched:
            return ExitEvent("ENTRY_BAR_STOP", stop_price, ambiguous=target_touched)
        if target_touched:
            return ExitEvent("ENTRY_BAR_TAKE_PROFIT", target_price)
        return None
    raise ConfigurationError(f"Unsupported intrabar policy: {intrabar_policy}")


def build_entry_candidate(
    *,
    ticker: str,
    candle_index: int,
    series: MarketSeries,
    as_of: datetime,
    shield_cache: Mapping[str, Any],
    optimized_cache: Mapping[str, Any],
    dea_cache: Mapping[str, Any],
    tickers: Sequence[str],
    factor_mode: str,
    dpi_bearish: bool,
    atr_period: int,
    default_atr_multiplier: float,
    forced_multiplier: float | None,
    price_decimals: int,
) -> EntryCandidate | None:
    if candle_index <= 0:
        return None

    previous = series.candles[candle_index - 1]
    previous_atr = calculate_atr(series.candles, candle_index - 1, atr_period)
    if previous_atr is None:
        return None

    optimized_record, optimized_temporal = resolve_factor_record(
        optimized_cache, ticker, as_of, factor_mode
    )
    shield_record, shield_temporal = resolve_factor_record(
        shield_cache, ticker, as_of, factor_mode
    )
    dea_multiplier, dea_temporal = compute_dea_size_multiplier(
        ticker, dea_cache, tickers, as_of, factor_mode
    )

    beta = max(0.0, _safe_float(optimized_record, "beta", BETA_THRESHOLD))
    trailing_multiplier = select_trailing_multiplier(
        beta, optimized_record, default_atr_multiplier, forced_multiplier
    )
    adjustment = get_signal_adjustment(shield_record, dpi_bearish)

    pullback_pct = _safe_float(
        optimized_record,
        "entry_pullback_pct",
        _safe_float(optimized_record, "total_return_pct", 2.0),
    )
    pullback_pct = max(0.0, pullback_pct) / 100.0
    base_discount = max(previous_atr, previous.close * pullback_pct)
    # Positive (bullish) adjustment allows a slightly shallower discount;
    # negative adjustment requires a deeper discount.
    adjusted_discount = base_discount * (1.0 - adjustment)
    entry_limit = round_price(previous.close - adjusted_discount, price_decimals)
    if not is_finite_positive(entry_limit) or entry_limit >= previous.close:
        return None

    current = series.candles[candle_index]
    if current.low > entry_limit:
        return None
    raw_entry_price = current.open if current.open <= entry_limit else entry_limit
    raw_entry_price = round_price(raw_entry_price, price_decimals)

    initial_stop = round_price(
        raw_entry_price - previous_atr * trailing_multiplier, price_decimals
    )
    if initial_stop <= 0 or initial_stop >= raw_entry_price:
        return None

    take_profit_pct = _safe_float(
        optimized_record,
        "take_profit_pct",
        _safe_float(optimized_record, "total_return_pct", 0.0),
    )
    if take_profit_pct > 0:
        base_target_distance = max(
            previous_atr, raw_entry_price * take_profit_pct / 100.0
        )
    else:
        base_target_distance = 3.0 * previous_atr
    target_distance = base_target_distance * (1.0 + adjustment)
    target_distance = max(previous_atr, target_distance)
    target_price = round_price(raw_entry_price + target_distance, price_decimals)

    shield_record_for_catalyst = shield_record
    catalyst_score = _safe_float(shield_record_for_catalyst, "catalyst_score", 0.0)
    if catalyst_score >= 50.0:
        catalyst_multiplier = 1.50
    elif catalyst_score >= 30.0:
        catalyst_multiplier = 1.25
    else:
        catalyst_multiplier = 1.00

    regime_multiplier = 0.50 if beta >= BETA_THRESHOLD else 1.00
    temporal_used = optimized_temporal or shield_temporal or dea_temporal

    return EntryCandidate(
        ticker=ticker,
        candle_index=candle_index,
        raw_entry_price=raw_entry_price,
        entry_limit=entry_limit,
        previous_atr=previous_atr,
        beta=beta,
        trailing_multiplier=trailing_multiplier,
        target_price=target_price,
        initial_stop=initial_stop,
        dea_multiplier=dea_multiplier,
        catalyst_multiplier=catalyst_multiplier,
        regime_multiplier=regime_multiplier,
        factor_adjustment=adjustment,
        point_in_time_record_used=temporal_used,
    )


def transaction_costs(gross_value: float, fee_rate: float, slippage_rate: float) -> tuple[float, float]:
    return gross_value * fee_rate, gross_value * slippage_rate


def liquidation_value(
    position: Position,
    price: float,
    fee_rate: float,
    slippage_rate: float,
) -> float:
    gross = position.shares * price
    fee, slippage = transaction_costs(gross, fee_rate, slippage_rate)
    return gross - fee - slippage


def gross_portfolio_equity(
    cash: float,
    positions: Mapping[str, Position],
    prices: Mapping[str, float],
) -> float:
    return cash + sum(
        position.shares * prices.get(ticker, position.entry_price)
        for ticker, position in positions.items()
    )


def portfolio_equity(
    cash: float,
    positions: Mapping[str, Position],
    prices: Mapping[str, float],
    fee_rate: float,
    slippage_rate: float,
) -> float:
    """Return net liquidation-equivalent equity after estimated sell costs."""
    equity = cash
    for ticker, position in positions.items():
        price = prices.get(ticker, position.entry_price)
        equity += liquidation_value(position, price, fee_rate, slippage_rate)
    return equity


def estimated_position_risk(
    position: Position, fee_rate: float, slippage_rate: float
) -> float:
    """Estimated total loss if the active stop fills without a gap."""
    stop_net = liquidation_value(
        position, position.stop_price, fee_rate, slippage_rate
    )
    return max(0.0, position.entry_outlay - stop_net)


def portfolio_open_risk(
    positions: Mapping[str, Position], fee_rate: float, slippage_rate: float
) -> float:
    return sum(
        estimated_position_risk(position, fee_rate, slippage_rate)
        for position in positions.values()
    )


def candidate_risk_per_share(
    candidate: EntryCandidate, fee_rate: float, slippage_rate: float
) -> float:
    entry_outlay = candidate.raw_entry_price * (1.0 + fee_rate + slippage_rate)
    stop_net = candidate.initial_stop * (1.0 - fee_rate - slippage_rate)
    return max(0.0, entry_outlay - stop_net)


def calculate_target_shares(
    candidate: EntryCandidate,
    *,
    cash: float,
    equity: float,
    risk_per_trade: float,
    max_position_pct: float,
    fee_rate: float,
    slippage_rate: float,
    max_risk_dollars: float | None = None,
) -> int:
    risk_per_share = candidate_risk_per_share(
        candidate, fee_rate, slippage_rate
    )
    if risk_per_share <= 0:
        return 0

    risk_budget = equity * risk_per_trade
    risk_budget *= (
        candidate.catalyst_multiplier
        * candidate.regime_multiplier
        * candidate.dea_multiplier
    )
    if max_risk_dollars is not None:
        risk_budget = min(risk_budget, max(0.0, max_risk_dollars))
    max_by_risk = math.floor(risk_budget / risk_per_share)

    max_capital = equity * max_position_pct
    max_by_cap = math.floor(max_capital / candidate.raw_entry_price)

    all_in_per_share = candidate.raw_entry_price * (1.0 + fee_rate + slippage_rate)
    max_by_cash = math.floor(cash / all_in_per_share)

    return max(0, min(max_by_risk, max_by_cap, max_by_cash))


def execute_buy(
    candidate: EntryCandidate,
    *,
    shares: int,
    timestamp: datetime,
    fee_rate: float,
    slippage_rate: float,
) -> tuple[Position, dict[str, Any]]:
    gross = shares * candidate.raw_entry_price
    fee, slippage = transaction_costs(gross, fee_rate, slippage_rate)
    outlay = gross + fee + slippage
    trade_id = f"{candidate.ticker}-{timestamp.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"

    position = Position(
        trade_id=trade_id,
        ticker=candidate.ticker,
        shares=shares,
        entry_time=timestamp,
        entry_price=candidate.raw_entry_price,
        entry_gross=gross,
        entry_fee=fee,
        entry_slippage=slippage,
        entry_outlay=outlay,
        entry_atr=candidate.previous_atr,
        trailing_multiplier=candidate.trailing_multiplier,
        highest_seen=candidate.raw_entry_price,
        stop_price=candidate.initial_stop,
        target_price=candidate.target_price,
        beta=candidate.beta,
    )
    transaction = {
        "trade_id": trade_id,
        "timestamp": timestamp.isoformat(),
        "date": timestamp.date().isoformat(),
        "action": "BUY",
        "ticker": candidate.ticker,
        "shares": shares,
        "price": candidate.raw_entry_price,
        "gross_value": gross,
        "fee": fee,
        "slippage": slippage,
        "value": outlay,
        "realized_pnl": None,
        "reason": "LIMIT_ENTRY",
        "dea_multiplier": candidate.dea_multiplier,
        "catalyst_multiplier": candidate.catalyst_multiplier,
        "regime_multiplier": candidate.regime_multiplier,
        "factor_adjustment": candidate.factor_adjustment,
        "point_in_time_record_used": candidate.point_in_time_record_used,
        "notes": (
            f"Limit {candidate.entry_limit:.4f}; initial stop "
            f"{candidate.initial_stop:.4f}; target {candidate.target_price:.4f}"
        ),
    }
    return position, transaction


def execute_sell(
    position: Position,
    event: ExitEvent,
    *,
    timestamp: datetime,
    fee_rate: float,
    slippage_rate: float,
) -> tuple[float, dict[str, Any], dict[str, Any]]:
    gross = position.shares * event.raw_price
    fee, slippage = transaction_costs(gross, fee_rate, slippage_rate)
    net_proceeds = gross - fee - slippage
    realized_pnl = net_proceeds - position.entry_outlay
    return_pct = (
        realized_pnl / position.entry_outlay * 100.0
        if position.entry_outlay > 0
        else 0.0
    )

    transaction = {
        "trade_id": position.trade_id,
        "timestamp": timestamp.isoformat(),
        "date": timestamp.date().isoformat(),
        "action": "SELL",
        "ticker": position.ticker,
        "shares": position.shares,
        "price": event.raw_price,
        "gross_value": gross,
        "fee": fee,
        "slippage": slippage,
        "value": net_proceeds,
        "realized_pnl": realized_pnl,
        "reason": event.reason,
        "dea_multiplier": None,
        "catalyst_multiplier": None,
        "regime_multiplier": None,
        "factor_adjustment": None,
        "point_in_time_record_used": None,
        "notes": "Ambiguous OHLC path" if event.ambiguous else "",
    }
    completed = {
        "trade_id": position.trade_id,
        "ticker": position.ticker,
        "entry_time": position.entry_time.isoformat(),
        "exit_time": timestamp.isoformat(),
        "shares": position.shares,
        "entry_price": position.entry_price,
        "exit_price": event.raw_price,
        "entry_outlay": position.entry_outlay,
        "net_proceeds": net_proceeds,
        "realized_pnl": realized_pnl,
        "return_pct": return_pct,
        "exit_reason": event.reason,
        "ambiguous_bar": event.ambiguous,
    }
    return net_proceeds, transaction, completed


def update_position_after_bar(
    position: Position,
    candle: Candle,
    current_atr: float | None,
    *,
    loser_leash: bool,
    entered_this_bar: bool,
    entered_at_open: bool,
    intrabar_policy: str,
    price_decimals: int,
) -> None:
    """Update high-water mark and a next-bar stop without ever loosening it."""
    if entered_this_bar and not entered_at_open:
        if intrabar_policy in {"olhc", "optimistic"}:
            observed_high = candle.high
        else:
            observed_high = max(position.entry_price, candle.close)
    else:
        observed_high = candle.high

    position.highest_seen = max(position.highest_seen, observed_high)
    if current_atr is None:
        return

    if loser_leash and candle.close < position.entry_price:
        atr_pct = current_atr / position.entry_price
        leash_pct = max(
            LOSER_LEASH_MIN_PCT,
            min(LOSER_LEASH_MAX_PCT, LOSER_LEASH_ATR_FACTOR * atr_pct),
        )
        candidate_stop = position.entry_price * (1.0 - leash_pct)
    else:
        candidate_stop = (
            position.highest_seen - current_atr * position.trailing_multiplier
        )

    # The revised stop becomes active on the next bar.  Clamping it to the
    # completed bar's close avoids creating a stop that was already above the
    # market when the bar ended.
    candidate_stop = min(candidate_stop, candle.close)
    candidate_stop = round_price(candidate_stop, price_decimals)
    if candidate_stop > 0:
        position.stop_price = max(position.stop_price, candidate_stop)


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------


def periods_per_year(timeframe: str) -> float:
    text = timeframe.strip().upper()
    if text in {"D", "1D", "DAY", "DAILY"}:
        return 252.0
    if text in {"W", "1W", "WEEK", "WEEKLY"}:
        return 52.0
    if text in {"H", "1H", "60M"}:
        return 252.0 * 6.5
    if text.endswith("H") and text[:-1].isdigit():
        hours = int(text[:-1])
        return 252.0 * max(1.0, 6.5 / hours)
    if text.endswith("M") and text[:-1].isdigit():
        minutes = int(text[:-1])
        return 252.0 * max(1.0, 390.0 / minutes)
    if text.isdigit():
        minutes = int(text)
        if minutes > 0:
            return 252.0 * max(1.0, 390.0 / minutes)
    return 252.0


def calculate_performance_metrics(
    *,
    initial_cash: float,
    final_cash: float,
    final_equity: float,
    positions: Mapping[str, Position],
    last_prices: Mapping[str, float],
    completed_trades: Sequence[Mapping[str, Any]],
    transactions: Sequence[Mapping[str, Any]],
    equity_curve: Sequence[Mapping[str, Any]],
    timeframe: str,
    risk_free_rate: float,
    fee_rate: float,
    slippage_rate: float,
) -> dict[str, Any]:
    total_return_pct = (final_equity / initial_cash - 1.0) * 100.0
    realized_pnl = sum(float(trade["realized_pnl"]) for trade in completed_trades)

    unrealized_pnl = 0.0
    for ticker, position in positions.items():
        mark = last_prices.get(ticker, position.entry_price)
        unrealized_pnl += (
            liquidation_value(position, mark, fee_rate, slippage_rate)
            - position.entry_outlay
        )

    wins = [trade for trade in completed_trades if float(trade["realized_pnl"]) > 0]
    losses = [trade for trade in completed_trades if float(trade["realized_pnl"]) < 0]
    completed_count = len(completed_trades)
    win_rate_pct = len(wins) / completed_count * 100.0 if completed_count else 0.0
    gross_profit = sum(float(trade["realized_pnl"]) for trade in wins)
    gross_loss = -sum(float(trade["realized_pnl"]) for trade in losses)
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else None
    average_win = gross_profit / len(wins) if wins else 0.0
    average_loss = -gross_loss / len(losses) if losses else 0.0
    expectancy = realized_pnl / completed_count if completed_count else 0.0

    values = [
        float(row.get("liquidation_equity", row.get("equity", initial_cash)))
        for row in equity_curve
    ]
    max_drawdown_pct = 0.0
    running_peak = initial_cash
    for value in values:
        running_peak = max(running_peak, value)
        if running_peak > 0:
            drawdown = (running_peak - value) / running_peak * 100.0
            max_drawdown_pct = max(max_drawdown_pct, drawdown)

    sharpe = 0.0
    returns = [
        values[index] / values[index - 1] - 1.0
        for index in range(1, len(values))
        if values[index - 1] != 0
    ]
    if len(returns) >= 2:
        mean_return = sum(returns) / len(returns)
        variance = sum(
            (value - mean_return) ** 2 for value in returns
        ) / (len(returns) - 1)
        standard_deviation = math.sqrt(variance)
        if standard_deviation > 0 and math.isfinite(standard_deviation):
            annual_periods = periods_per_year(timeframe)
            risk_free_per_period = (1.0 + risk_free_rate) ** (1.0 / annual_periods) - 1.0
            excess_mean = mean_return - risk_free_per_period
            sharpe = excess_mean / standard_deviation * math.sqrt(annual_periods)

    total_fees = sum(float(tx.get("fee", 0.0) or 0.0) for tx in transactions)
    total_slippage = sum(
        float(tx.get("slippage", 0.0) or 0.0) for tx in transactions
    )

    return {
        "initial_cash": initial_cash,
        "final_cash": final_cash,
        "final_equity": final_equity,
        "total_return_pct": total_return_pct,
        "realized_pnl": realized_pnl,
        "unrealized_pnl": unrealized_pnl,
        "transaction_legs": len(transactions),
        "completed_round_trips": completed_count,
        "open_positions": len(positions),
        "win_rate_pct": win_rate_pct,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": profit_factor,
        "average_win": average_win,
        "average_loss": average_loss,
        "expectancy_per_completed_trade": expectancy,
        "max_drawdown_pct": max_drawdown_pct,
        "sharpe": sharpe,
        "total_fees": total_fees,
        "total_slippage": total_slippage,
    }


# ---------------------------------------------------------------------------
# Replay engine
# ---------------------------------------------------------------------------


def run_replay(args: argparse.Namespace) -> ReplayArtifacts:
    start = parse_date_start(args.start_date)
    end_exclusive = start + timedelta(days=args.days)
    tickers = sorted({ticker.strip().upper() for ticker in args.tickers.split(",") if ticker.strip()})
    if not tickers:
        raise ConfigurationError("At least one ticker is required")

    data_dir = Path(args.data_dir).expanduser().resolve() if args.data_dir else None
    if data_dir and not data_dir.is_dir():
        raise ConfigurationError(f"--data-dir is not a directory: {data_dir}")

    exchange_cache = load_json(Path(args.exchange_cache))
    shield_cache = load_json(Path(args.shield_file))
    optimized_cache = load_json(Path(args.optimized_file))
    dea_cache = load_json(Path(args.dea_file))
    for name, value in {
        "exchange cache": exchange_cache,
        "shield cache": shield_cache,
        "optimized cache": optimized_cache,
        "DEA cache": dea_cache,
    }.items():
        if not isinstance(value, Mapping):
            LOGGER.warning("%s is not a JSON object; ignoring it", name)
    exchange_cache = (
        normalize_ticker_cache(exchange_cache)
        if isinstance(exchange_cache, Mapping)
        else {}
    )
    shield_cache = (
        normalize_ticker_cache(shield_cache)
        if isinstance(shield_cache, Mapping)
        else {}
    )
    optimized_cache = (
        normalize_ticker_cache(optimized_cache)
        if isinstance(optimized_cache, Mapping)
        else {}
    )
    dea_cache = (
        normalize_ticker_cache(dea_cache)
        if isinstance(dea_cache, Mapping)
        else {}
    )

    factor_snapshot_records_loaded = 0
    if args.factor_snapshot_dir:
        snapshot_caches, factor_snapshot_records_loaded = load_factor_snapshot_directory(
            Path(args.factor_snapshot_dir).expanduser().resolve()
        )
        shield_cache = merge_factor_cache(shield_cache, snapshot_caches["shield"])
        optimized_cache = merge_factor_cache(
            optimized_cache, snapshot_caches["optimized"]
        )
        dea_cache = merge_factor_cache(dea_cache, snapshot_caches["dea"])

    api_key = os.environ.get(args.api_key_env)
    range_count = args.range_count or estimate_range_count(
        args.days, args.timeframe, args.atr_period
    )

    LOGGER.info("Kurt Replay Sandbox v%s", VERSION)
    LOGGER.info(
        "Replay range: %s to %s (exclusive), tickers=%s, timeframe=%s",
        start.isoformat(),
        end_exclusive.isoformat(),
        ",".join(tickers),
        args.timeframe,
    )

    market_data: dict[str, MarketSeries] = {}
    warnings: list[str] = []
    for ticker in tickers:
        series = build_market_series(
            ticker,
            exchange_cache=exchange_cache,
            data_dir=data_dir,
            api_key=api_key,
            rapidapi_host=args.rapidapi_host,
            timeframe=args.timeframe,
            range_count=range_count,
            start=start,
            end_exclusive=end_exclusive,
            atr_period=args.atr_period,
            timeout_seconds=args.request_timeout,
            retries=args.request_retries,
        )
        market_data[ticker] = series
        if series.invalid_rows:
            warnings.append(
                f"{ticker}: discarded {series.invalid_rows} invalid candle row(s)."
            )
        if series.duplicate_rows:
            warnings.append(
                f"{ticker}: replaced {series.duplicate_rows} duplicate timestamp row(s)."
            )

    undated_factor_records_ignored = 0
    if args.factor_mode == "static":
        warnings.append(
            "Static auxiliary factor mode was enabled. Undated current records may "
            "introduce look-ahead bias into historical results."
        )
    elif args.factor_mode == "point-in-time":
        undated_factor_records_ignored = count_undated_factor_records(
            [shield_cache, optimized_cache, dea_cache], tickers
        )
        if undated_factor_records_ignored:
            warnings.append(
                f"Point-in-time mode ignored {undated_factor_records_ignored} "
                "undated auxiliary ticker record(s). Supply dated snapshots or use "
                "--factor-mode static only for explicitly non-causal exploratory runs."
            )

    timeline = sorted(
        {
            series.candles[index].timestamp
            for series in market_data.values()
            for index in series.sim_indices
        }
    )
    if not timeline:
        raise DataSourceError("No replay timestamps were available")

    cash = args.initial_cash
    positions: dict[str, Position] = {}
    transactions: list[dict[str, Any]] = []
    completed_trades: list[dict[str, Any]] = []
    equity_curve: list[dict[str, Any]] = []
    last_close: dict[str, float] = {}

    candidate_signals = 0
    skipped_entry_signals = 0
    skipped_position_limit = 0
    skipped_portfolio_risk = 0
    ambiguous_exit_bars = 0
    max_open_risk_observed = 0.0

    fee_rate = args.commission_bps / 10_000.0
    slippage_rate = args.slippage_bps / 10_000.0

    for timestamp in timeline:
        bars: dict[str, tuple[int, Candle]] = {}
        for ticker, series in market_data.items():
            candle_index = series.index_by_time.get(timestamp)
            if candle_index is not None:
                bars[ticker] = (candle_index, series.candles[candle_index])

        # Valuation for sizing uses only previously completed bars, preventing
        # the current bar's close from leaking into an intrabar decision.
        pre_bar_prices = dict(last_close)
        cash_at_bar_open = cash
        exited_this_bar: set[str] = set()
        entered_this_bar: dict[str, bool] = {}

        # Phase 1: exits for positions that existed before this bar.
        for ticker in sorted(list(positions)):
            if ticker not in bars:
                continue
            _, candle = bars[ticker]
            position = positions[ticker]
            event = resolve_existing_exit(
                candle,
                position.stop_price,
                position.target_price,
                args.intrabar_policy,
            )
            if event is None:
                continue
            if event.ambiguous:
                ambiguous_exit_bars += 1
            proceeds, transaction, completed = execute_sell(
                position,
                event,
                timestamp=timestamp,
                fee_rate=fee_rate,
                slippage_rate=slippage_rate,
            )
            cash += proceeds
            transactions.append(transaction)
            completed_trades.append(completed)
            del positions[ticker]
            exited_this_bar.add(ticker)
            LOGGER.info(
                "%s %s: sold %d at %.4f, P&L %.2f",
                event.reason,
                ticker,
                position.shares,
                event.raw_price,
                completed["realized_pnl"],
            )

        # Phase 2: collect candidates, then execute in a deterministic order.
        # By default, cash generated by exits on this same OHLC bar cannot fund
        # another same-bar entry because the cross-symbol event order is unknown.
        entry_cash_budget = cash if args.reuse_same_bar_exit_cash else cash_at_bar_open
        candidates: list[EntryCandidate] = []
        for ticker in tickers:
            if ticker in positions or ticker in exited_this_bar or ticker not in bars:
                continue
            candle_index, _ = bars[ticker]
            previous_timestamp = market_data[ticker].candles[candle_index - 1].timestamp
            candidate = build_entry_candidate(
                ticker=ticker,
                candle_index=candle_index,
                series=market_data[ticker],
                as_of=previous_timestamp,
                shield_cache=shield_cache,
                optimized_cache=optimized_cache,
                dea_cache=dea_cache,
                tickers=tickers,
                factor_mode=args.factor_mode,
                dpi_bearish=args.dpi_bearish,
                atr_period=args.atr_period,
                default_atr_multiplier=args.atr_multiplier,
                forced_multiplier=args.force_multiplier,
                price_decimals=args.price_decimals,
            )
            if candidate is not None:
                candidate_signals += 1
                candidates.append(candidate)

        # Highest risk-adjusted factor score first; ticker breaks ties.  The
        # ordering is explicit and deterministic when simultaneous entries
        # compete for limited cash.
        candidates.sort(
            key=lambda candidate: (
                -candidate.dea_multiplier
                * candidate.catalyst_multiplier
                * candidate.regime_multiplier,
                candidate.ticker,
            )
        )

        for candidate in candidates:
            if candidate.ticker in positions:
                continue
            if len(positions) >= args.max_positions:
                skipped_entry_signals += 1
                skipped_position_limit += 1
                LOGGER.debug(
                    "Skipping %s: maximum position count reached", candidate.ticker
                )
                continue

            equity_for_sizing = portfolio_equity(
                cash,
                positions,
                pre_bar_prices,
                fee_rate,
                slippage_rate,
            )
            current_open_risk = portfolio_open_risk(
                positions, fee_rate, slippage_rate
            )
            max_open_risk = equity_for_sizing * args.max_portfolio_risk_pct
            remaining_open_risk = max(0.0, max_open_risk - current_open_risk)
            available_entry_cash = (
                cash
                if args.reuse_same_bar_exit_cash
                else min(cash, entry_cash_budget)
            )
            shares = calculate_target_shares(
                candidate,
                cash=available_entry_cash,
                equity=equity_for_sizing,
                risk_per_trade=args.risk_per_trade,
                max_position_pct=args.max_position_pct,
                fee_rate=fee_rate,
                slippage_rate=slippage_rate,
                max_risk_dollars=remaining_open_risk,
            )
            if shares <= 0:
                skipped_entry_signals += 1
                if remaining_open_risk < candidate_risk_per_share(
                    candidate, fee_rate, slippage_rate
                ):
                    skipped_portfolio_risk += 1
                LOGGER.debug("Skipping %s: sizing produced zero shares", candidate.ticker)
                continue

            position, buy_transaction = execute_buy(
                candidate,
                shares=shares,
                timestamp=timestamp,
                fee_rate=fee_rate,
                slippage_rate=slippage_rate,
            )
            if position.entry_outlay > cash + 1e-9:
                LOGGER.debug("Skipping %s: insufficient cash after costs", candidate.ticker)
                continue
            cash -= position.entry_outlay
            if not args.reuse_same_bar_exit_cash:
                entry_cash_budget = max(0.0, entry_cash_budget - position.entry_outlay)
            positions[candidate.ticker] = position
            transactions.append(buy_transaction)
            candle = market_data[candidate.ticker].candles[candidate.candle_index]
            entered_at_open = candle.open <= candidate.entry_limit
            entered_this_bar[candidate.ticker] = entered_at_open
            LOGGER.info(
                "LIMIT_ENTRY %s: bought %d at %.4f, stop %.4f, target %.4f",
                candidate.ticker,
                shares,
                position.entry_price,
                position.stop_price,
                position.target_price,
            )

            same_bar_event = resolve_entry_bar_exit(
                candle,
                entry_limit=candidate.entry_limit,
                stop_price=position.stop_price,
                target_price=position.target_price,
                intrabar_policy=args.intrabar_policy,
            )
            if same_bar_event is not None:
                if same_bar_event.ambiguous:
                    ambiguous_exit_bars += 1
                proceeds, sell_transaction, completed = execute_sell(
                    position,
                    same_bar_event,
                    timestamp=timestamp,
                    fee_rate=fee_rate,
                    slippage_rate=slippage_rate,
                )
                cash += proceeds
                transactions.append(sell_transaction)
                completed_trades.append(completed)
                del positions[candidate.ticker]
                exited_this_bar.add(candidate.ticker)
                LOGGER.info(
                    "%s %s on entry bar: P&L %.2f",
                    same_bar_event.reason,
                    candidate.ticker,
                    completed["realized_pnl"],
                )

        # Phase 3: close-of-bar state updates; revised stops activate next bar.
        for ticker, position in list(positions.items()):
            if ticker not in bars:
                continue
            candle_index, candle = bars[ticker]
            current_atr = calculate_atr(
                market_data[ticker].candles, candle_index, args.atr_period
            )
            update_position_after_bar(
                position,
                candle,
                current_atr,
                loser_leash=args.loser_leash,
                entered_this_bar=ticker in entered_this_bar,
                entered_at_open=entered_this_bar.get(ticker, False),
                intrabar_policy=args.intrabar_policy,
                price_decimals=args.price_decimals,
            )

        # Phase 4: publish completed closing prices and record net liquidation equity.
        for ticker, (_, candle) in bars.items():
            last_close[ticker] = candle.close
        gross_equity = gross_portfolio_equity(cash, positions, last_close)
        liquidation_equity = portfolio_equity(
            cash,
            positions,
            last_close,
            fee_rate,
            slippage_rate,
        )
        current_open_risk = portfolio_open_risk(
            positions, fee_rate, slippage_rate
        )
        max_open_risk_observed = max(max_open_risk_observed, current_open_risk)
        equity_curve.append(
            {
                "timestamp": timestamp.isoformat(),
                "cash": cash,
                "gross_equity": gross_equity,
                "liquidation_equity": liquidation_equity,
                "equity": liquidation_equity,  # compatibility alias
                "open_risk": current_open_risk,
                "open_positions": len(positions),
            }
        )

    final_gross_equity = gross_portfolio_equity(cash, positions, last_close)
    final_equity = portfolio_equity(
        cash,
        positions,
        last_close,
        fee_rate,
        slippage_rate,
    )
    metrics = calculate_performance_metrics(
        initial_cash=args.initial_cash,
        final_cash=cash,
        final_equity=final_equity,
        positions=positions,
        last_prices=last_close,
        completed_trades=completed_trades,
        transactions=transactions,
        equity_curve=equity_curve,
        timeframe=args.timeframe,
        risk_free_rate=args.risk_free_rate,
        fee_rate=fee_rate,
        slippage_rate=slippage_rate,
    )

    metrics.update(
        {
            "final_gross_equity": final_gross_equity,
            "candidate_entry_signals": candidate_signals,
            "skipped_entry_signals": skipped_entry_signals,
            "skipped_position_limit": skipped_position_limit,
            "skipped_portfolio_risk": skipped_portfolio_risk,
            "ambiguous_exit_bars": ambiguous_exit_bars,
            "max_open_risk_observed": max_open_risk_observed,
        }
    )
    if ambiguous_exit_bars:
        warnings.append(
            f"{ambiguous_exit_bars} exit event(s) occurred on bars with ambiguous "
            f"high/low ordering and were resolved using {args.intrabar_policy!r}."
        )

    run_id = utc_now().strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    coverage = {
        ticker: {
            "symbol": series.symbol,
            "source": series.source,
            "first_replay_bar": series.first_sim_timestamp.isoformat(),
            "last_replay_bar": series.last_sim_timestamp.isoformat(),
            "replay_bar_count": len(series.sim_indices),
            "invalid_rows_discarded": series.invalid_rows,
            "duplicate_rows_replaced": series.duplicate_rows,
        }
        for ticker, series in market_data.items()
    }

    summary = {
        "schema_version": "2.0",
        "program_version": VERSION,
        "run_id": run_id,
        "generated_at_utc": utc_now().isoformat(),
        "tickers": tickers,
        "start_date": args.start_date,
        "end_exclusive": end_exclusive.date().isoformat(),
        "end_date": (end_exclusive - timedelta(days=1)).date().isoformat(),
        "calendar_days_requested": args.days,
        "timeframe": args.timeframe,
        "intrabar_policy": args.intrabar_policy,
        "factor_mode": args.factor_mode,
        "factor_snapshot_directory": args.factor_snapshot_dir,
        "factor_snapshot_records_loaded": factor_snapshot_records_loaded,
        "point_in_time_safe": args.factor_mode != "static",
        "undated_factor_records_ignored": undated_factor_records_ignored,
        "atr_period": args.atr_period,
        "default_atr_multiplier": args.atr_multiplier,
        "forced_atr_multiplier": args.force_multiplier,
        "loser_leash": args.loser_leash,
        "dpi_bearish": args.dpi_bearish,
        "risk_per_trade": args.risk_per_trade,
        "max_position_pct": args.max_position_pct,
        "max_portfolio_risk_pct": args.max_portfolio_risk_pct,
        "max_positions": args.max_positions,
        "reuse_same_bar_exit_cash": args.reuse_same_bar_exit_cash,
        "commission_bps_per_side": args.commission_bps,
        "slippage_bps_per_side": args.slippage_bps,
        "risk_free_rate": args.risk_free_rate,
        "coverage": coverage,
        "metrics": metrics,
        # Compatibility aliases for existing consumers.
        "initial_cash": metrics["initial_cash"],
        "final_cash": metrics["final_cash"],
        "final_equity": metrics["final_equity"],
        "total_return_pct": metrics["total_return_pct"],
        "win_rate_pct": metrics["win_rate_pct"],
        "trades_count": metrics["transaction_legs"],
        "completed_trades_count": metrics["completed_round_trips"],
        "max_dd_pct": metrics["max_drawdown_pct"],
        "sharpe": metrics["sharpe"],
        "warnings": warnings,
    }

    return ReplayArtifacts(
        run_id=run_id,
        summary=summary,
        positions=positions,
        transactions=transactions,
        completed_trades=completed_trades,
        equity_curve=equity_curve,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Artifact persistence and optional Google Sheets publication
# ---------------------------------------------------------------------------


def transaction_columns() -> list[str]:
    return [
        "trade_id",
        "timestamp",
        "date",
        "action",
        "ticker",
        "shares",
        "price",
        "gross_value",
        "fee",
        "slippage",
        "value",
        "realized_pnl",
        "reason",
        "dea_multiplier",
        "catalyst_multiplier",
        "regime_multiplier",
        "factor_adjustment",
        "point_in_time_record_used",
        "notes",
    ]


def completed_trade_columns() -> list[str]:
    return [
        "trade_id",
        "ticker",
        "entry_time",
        "exit_time",
        "shares",
        "entry_price",
        "exit_price",
        "entry_outlay",
        "net_proceeds",
        "realized_pnl",
        "return_pct",
        "exit_reason",
        "ambiguous_bar",
    ]


def persist_artifacts(artifacts: ReplayArtifacts, output_dir: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    run_dir = output_dir / "runs" / artifacts.run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    positions_payload = {
        ticker: position.to_json() for ticker, position in artifacts.positions.items()
    }

    latest_paths = {
        "positions": output_dir / "replay_positions.json",
        "summary": output_dir / "replay_summary.json",
        "history": output_dir / "replay_history.json",
        "transactions": output_dir / "replay_transactions.csv",
        "completed_trades": output_dir / "replay_completed_trades.csv",
        "closed_trades_alias": output_dir / "replay_closed_trades.csv",
        "equity_curve": output_dir / "replay_equity_curve.csv",
    }
    run_paths = {
        "positions": run_dir / "positions.json",
        "summary": run_dir / "summary.json",
        "transactions": run_dir / "transactions.csv",
        "completed_trades": run_dir / "completed_trades.csv",
        "equity_curve": run_dir / "equity_curve.csv",
    }

    for path in (latest_paths["positions"], run_paths["positions"]):
        save_json_atomic(positions_payload, path)
    for path in (
        latest_paths["summary"],
        latest_paths["history"],
        run_paths["summary"],
    ):
        save_json_atomic(artifacts.summary, path)
    for path in (latest_paths["transactions"], run_paths["transactions"]):
        save_csv_atomic(artifacts.transactions, path, transaction_columns())
    for path in (
        latest_paths["completed_trades"],
        latest_paths["closed_trades_alias"],
        run_paths["completed_trades"],
    ):
        save_csv_atomic(
            artifacts.completed_trades, path, completed_trade_columns()
        )
    equity_columns = [
        "timestamp",
        "cash",
        "gross_equity",
        "liquidation_equity",
        "equity",
        "open_risk",
        "open_positions",
    ]
    for path in (latest_paths["equity_curve"], run_paths["equity_curve"]):
        save_csv_atomic(artifacts.equity_curve, path, equity_columns)

    return {**latest_paths, "run_directory": run_dir}


def run_gog(
    args_list: Sequence[str],
    *,
    account: str,
    binary: str,
    expect_json: bool = False,
) -> Any:
    executable = shutil.which(binary)
    if executable is None:
        raise PublishError(f"Google Sheets CLI '{binary}' is not installed or not on PATH")

    environment = os.environ.copy()
    environment["GOG_ACCOUNT"] = account
    command = [executable, "sheets", *args_list]
    try:
        result = subprocess.run(
            command,
            env=environment,
            shell=False,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PublishError(f"Failed to execute {' '.join(command)}: {exc}") from exc

    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip() or "no error text"
        raise PublishError(
            f"Google Sheets command failed with exit code {result.returncode}: {stderr}"
        )
    if not expect_json:
        return result.stdout.strip()
    try:
        return json.loads(result.stdout.strip())
    except json.JSONDecodeError as exc:
        raise PublishError("Google Sheets metadata command returned invalid JSON") from exc


def ensure_tabs_exist(sheet_id: str, account: str, binary: str) -> None:
    metadata = run_gog(
        ["metadata", sheet_id, "--json"],
        account=account,
        binary=binary,
        expect_json=True,
    )
    sheets = metadata.get("sheets", []) if isinstance(metadata, Mapping) else []
    existing_titles = {
        str(sheet.get("properties", {}).get("title"))
        for sheet in sheets
        if isinstance(sheet, Mapping)
    }

    for title in ("Replay_Dashboard", "Replay_Leaderboard", "Replay_Current_Tx"):
        if title not in existing_titles:
            run_gog(
                ["add-tab", sheet_id, title],
                account=account,
                binary=binary,
            )

    if "Replay_Leaderboard" not in existing_titles:
        headers = [[
            "Timestamp",
            "Tickers",
            "Start Date",
            "End Exclusive",
            "Initial Cash",
            "Final Equity",
            "Total Return (%)",
            "Win Rate (%)",
            "Completed Trades",
            "Transaction Legs",
            "Max Drawdown (%)",
            "Sharpe",
            "Factor Mode",
            "Intrabar Policy",
        ]]
        run_gog(
            [
                "update",
                sheet_id,
                "Replay_Leaderboard!A1",
                f"--values-json={json.dumps(headers)}",
            ],
            account=account,
            binary=binary,
        )


def push_to_dashboard(
    summary: Mapping[str, Any],
    transactions: Sequence[Mapping[str, Any]],
    *,
    sheet_id: str,
    account: str,
    binary: str,
) -> None:
    ensure_tabs_exist(sheet_id, account, binary)
    metrics = summary["metrics"]

    dashboard = [
        ["Simulation Summary", "", "", ""],
        ["Program Version", summary["program_version"], "Run ID", summary["run_id"]],
        ["Initial Paper Cash", metrics["initial_cash"], "End Exclusive", summary["end_exclusive"]],
        ["Final Paper Cash", metrics["final_cash"], "Factor Mode", summary["factor_mode"]],
        ["Final Net Liquidation Equity", metrics["final_equity"], "Intrabar Policy", summary["intrabar_policy"]],
        ["Total Return (%)", metrics["total_return_pct"], "Point-in-Time Safe", summary["point_in_time_safe"]],
        ["Realized P&L", metrics["realized_pnl"], "Unrealized P&L", metrics["unrealized_pnl"]],
        ["Completed Round Trips", metrics["completed_round_trips"], "Transaction Legs", metrics["transaction_legs"]],
        ["Win Rate (%)", metrics["win_rate_pct"], "Profit Factor", metrics["profit_factor"]],
        ["Max Drawdown (%)", metrics["max_drawdown_pct"], "Sharpe", metrics["sharpe"]],
        ["Total Fees", metrics["total_fees"], "Total Slippage", metrics["total_slippage"]],
    ]
    run_gog(
        ["clear", sheet_id, "Replay_Dashboard!A1:D50"],
        account=account,
        binary=binary,
    )
    run_gog(
        [
            "update",
            sheet_id,
            "Replay_Dashboard!A1",
            f"--values-json={json.dumps(dashboard)}",
        ],
        account=account,
        binary=binary,
    )

    transaction_rows = [[
        "Timestamp",
        "Action",
        "Ticker",
        "Shares",
        "Price",
        "Gross",
        "Fee",
        "Slippage",
        "Net/Outlay",
        "Realized P&L",
        "Reason",
        "Trade ID",
    ]]
    for transaction in transactions:
        transaction_rows.append([
            transaction.get("timestamp", ""),
            transaction.get("action", ""),
            transaction.get("ticker", ""),
            transaction.get("shares", 0),
            transaction.get("price", 0.0),
            transaction.get("gross_value", 0.0),
            transaction.get("fee", 0.0),
            transaction.get("slippage", 0.0),
            transaction.get("value", 0.0),
            transaction.get("realized_pnl", ""),
            transaction.get("reason", ""),
            transaction.get("trade_id", ""),
        ])
    run_gog(
        ["clear", sheet_id, "Replay_Current_Tx!A1:L10000"],
        account=account,
        binary=binary,
    )
    run_gog(
        [
            "update",
            sheet_id,
            "Replay_Current_Tx!A1",
            f"--values-json={json.dumps(transaction_rows)}",
        ],
        account=account,
        binary=binary,
    )

    leaderboard_row = [[
        summary["generated_at_utc"],
        ", ".join(summary["tickers"]),
        summary["start_date"],
        summary["end_exclusive"],
        metrics["initial_cash"],
        metrics["final_equity"],
        round(metrics["total_return_pct"], 4),
        round(metrics["win_rate_pct"], 4),
        metrics["completed_round_trips"],
        metrics["transaction_legs"],
        round(metrics["max_drawdown_pct"], 4),
        round(metrics["sharpe"], 4),
        summary["factor_mode"],
        summary["intrabar_policy"],
    ]]
    run_gog(
        [
            "append",
            sheet_id,
            "Replay_Leaderboard!A:N",
            f"--values-json={json.dumps(leaderboard_row)}",
        ],
        account=account,
        binary=binary,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Kurt point-in-time-aware standalone replay service",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    parser.add_argument("--tickers", required=True, help="Comma-separated tickers")
    parser.add_argument(
        "--start-date",
        "--start_date",
        dest="start_date",
        required=True,
        help="Replay start date, YYYY-MM-DD",
    )
    parser.add_argument(
        "--days", type=int, default=60, help="Calendar-day duration; end is exclusive"
    )
    parser.add_argument(
        "--initial-cash",
        "--initial_cash",
        dest="initial_cash",
        type=float,
        default=20_000.0,
    )
    parser.add_argument("--timeframe", default="D", help="Candle timeframe such as D, 60, or 15")

    parser.add_argument("--atr-period", "--atr_period", dest="atr_period", type=int, default=14)
    parser.add_argument(
        "--atr-multiplier",
        "--atr_multiplier",
        dest="atr_multiplier",
        type=float,
        default=DEFAULT_ATR_MULTIPLIER,
    )
    parser.add_argument(
        "--force-multiplier",
        "--force_multiplier",
        dest="force_multiplier",
        type=float,
    )
    parser.add_argument(
        "--loser-leash",
        "--loser_leash",
        dest="loser_leash",
        nargs="?",
        const=True,
        default=True,
        type=parse_bool,
        help="Enable the close-based loser-leash stop update; accepts legacy True/False values",
    )
    parser.add_argument(
        "--no-loser-leash",
        "--no-loser_leash",
        dest="loser_leash",
        action="store_false",
        help="Disable the loser leash",
    )
    parser.add_argument(
        "--dpi-bearish",
        "--dpi_bearish",
        dest="dpi_bearish",
        nargs="?",
        const=True,
        default=True,
        type=parse_bool,
        help="Treat elevated DPI as a bearish distance adjustment; accepts legacy True/False values",
    )
    parser.add_argument(
        "--no-dpi-bearish",
        "--no-dpi_bearish",
        dest="dpi_bearish",
        action="store_false",
        help="Do not treat elevated DPI as bearish",
    )
    parser.add_argument(
        "--intrabar-policy",
        "--intrabar_policy",
        "--same-bar-policy",
        "--same_bar_policy",
        dest="intrabar_policy",
        type=parse_intrabar_policy,
        default="conservative",
        help="Ambiguous-bar policy: conservative/stop_first, optimistic/target_first, ohlc, or olhc",
    )
    parser.add_argument(
        "--factor-mode",
        "--factor_mode",
        dest="factor_mode",
        type=parse_factor_mode,
        default="point-in-time",
        help="off/neutral, point-in-time/snapshots, or static",
    )
    parser.add_argument(
        "--factor-snapshot-dir",
        "--factor_snapshot_dir",
        dest="factor_snapshot_dir",
        help="Optional directory of dated Quiver/optimized/DEA snapshot JSON files",
    )
    parser.add_argument(
        "--risk-per-trade",
        "--risk_per_trade",
        dest="risk_per_trade",
        type=float,
        default=0.01,
        help="Base fraction of equity risked per trade",
    )
    parser.add_argument(
        "--max-position-pct",
        "--max_position_pct",
        dest="max_position_pct",
        type=float,
        default=0.10,
        help="Maximum gross position value as a fraction of equity",
    )
    parser.add_argument(
        "--max-portfolio-risk-pct",
        "--max_portfolio_risk_pct",
        dest="max_portfolio_risk_pct",
        type=float,
        default=0.06,
        help="Maximum estimated aggregate stop risk as a fraction of equity",
    )
    parser.add_argument(
        "--max-positions",
        "--max_positions",
        dest="max_positions",
        type=int,
        default=10,
        help="Maximum concurrent positions",
    )
    parser.add_argument(
        "--reuse-same-bar-exit-cash",
        "--reuse_same_bar_exit_cash",
        dest="reuse_same_bar_exit_cash",
        action="store_true",
        help="Allow exit proceeds from an OHLC bar to fund another entry on that same bar",
    )
    parser.add_argument(
        "--commission-bps",
        "--commission_bps",
        "--cost-per-side-bps",
        "--cost_per_side_bps",
        dest="commission_bps",
        type=float,
        default=5.0,
        help="Commission/fee drag per side in basis points",
    )
    parser.add_argument(
        "--slippage-bps",
        "--slippage_bps",
        dest="slippage_bps",
        type=float,
        default=0.0,
        help="Additional adverse execution drag per side in basis points",
    )
    parser.add_argument(
        "--risk-free-rate",
        "--risk_free_rate",
        dest="risk_free_rate",
        type=float,
        default=DEFAULT_RISK_FREE_RATE,
        help="Annual risk-free rate used in Sharpe",
    )
    parser.add_argument(
        "--price-decimals",
        "--price_decimals",
        dest="price_decimals",
        type=int,
        default=2,
    )

    parser.add_argument(
        "--data-dir",
        "--data_dir",
        dest="data_dir",
        help="Optional directory containing TICKER.json or TICKER.csv candle files",
    )
    parser.add_argument(
        "--range-count",
        "--range_count",
        dest="range_count",
        type=int,
        help="Override the API range count",
    )
    parser.add_argument("--rapidapi-host", "--rapidapi_host", dest="rapidapi_host", default=DEFAULT_RAPIDAPI_HOST)
    parser.add_argument(
        "--api-key-env",
        "--api_key_env",
        dest="api_key_env",
        default="RAPIDAPI_KEY",
        help="Environment variable containing the RapidAPI key",
    )
    parser.add_argument(
        "--request-timeout",
        "--request_timeout",
        dest="request_timeout",
        type=float,
        default=20.0,
    )
    parser.add_argument(
        "--request-retries",
        "--request_retries",
        dest="request_retries",
        type=int,
        default=2,
    )

    parser.add_argument("--output-dir", "--output_dir", dest="output_dir", default=str(DEFAULT_SANDBOX_DIR))
    parser.add_argument("--exchange-cache", "--exchange_cache", dest="exchange_cache", default=str(DEFAULT_CACHE_FILE))
    parser.add_argument("--shield-file", "--shield_file", dest="shield_file", default=str(DEFAULT_SHIELD_FILE))
    parser.add_argument("--optimized-file", "--optimized_file", dest="optimized_file", default=str(DEFAULT_OPTIMIZED_FILE))
    parser.add_argument("--dea-file", "--dea_file", dest="dea_file", default=str(DEFAULT_DEA_SCORES_FILE))

    parser.add_argument(
        "--publish-sheets",
        "--publish-sheet",
        "--publish_sheet",
        dest="publish_sheets",
        action="store_true",
        help="Opt in to modifying a Google Sheet after local artifacts succeed",
    )
    parser.add_argument(
        "--sheet-required",
        "--sheet_required",
        dest="sheet_required",
        action="store_true",
        help="Return exit code 4 if an explicitly requested publication fails",
    )
    parser.add_argument("--sheet-id", "--sheet_id", dest="sheet_id", default=os.environ.get("REPLAY_SHEET_ID"))
    parser.add_argument("--gog-account", "--gog_account", dest="gog_account", default=os.environ.get("GOG_ACCOUNT"))
    parser.add_argument("--gog-binary", "--gog_binary", dest="gog_binary", default="gog")
    parser.add_argument(
        "--log-level",
        "--log_level",
        dest="log_level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
    )
    return parser


def validate_args(args: argparse.Namespace) -> None:
    positive_fields = {
        "days": args.days,
        "initial_cash": args.initial_cash,
        "atr_period": args.atr_period,
        "atr_multiplier": args.atr_multiplier,
        "request_timeout": args.request_timeout,
        "max_positions": args.max_positions,
    }
    for name, value in positive_fields.items():
        if value <= 0:
            raise ConfigurationError(f"--{name.replace('_', '-')} must be greater than zero")
    if args.force_multiplier is not None and args.force_multiplier <= 0:
        raise ConfigurationError("--force-multiplier must be greater than zero")
    for name, value in {
        "risk-per-trade": args.risk_per_trade,
        "max-position-pct": args.max_position_pct,
        "max-portfolio-risk-pct": args.max_portfolio_risk_pct,
    }.items():
        if not 0 < value <= 1:
            raise ConfigurationError(f"--{name} must be in (0, 1]")
    if args.commission_bps < 0 or args.slippage_bps < 0:
        raise ConfigurationError("Commission and slippage basis points cannot be negative")
    if args.risk_free_rate <= -1:
        raise ConfigurationError("--risk-free-rate must be greater than -1")
    if not 0 <= args.price_decimals <= 8:
        raise ConfigurationError("--price-decimals must be between 0 and 8")
    if args.request_retries < 0:
        raise ConfigurationError("--request-retries cannot be negative")
    if args.range_count is not None and args.range_count <= 0:
        raise ConfigurationError("--range-count must be greater than zero")
    if args.factor_snapshot_dir and args.factor_mode == "off":
        raise ConfigurationError(
            "--factor-snapshot-dir cannot be combined with --factor-mode off/neutral"
        )
    if args.sheet_required and not args.publish_sheets:
        raise ConfigurationError("--sheet-required requires --publish-sheets")
    if args.publish_sheets and (not args.sheet_id or not args.gog_account):
        raise ConfigurationError(
            "--publish-sheets requires --sheet-id and --gog-account (or "
            "REPLAY_SHEET_ID and GOG_ACCOUNT environment variables)"
        )


def print_summary(summary: Mapping[str, Any], artifact_paths: Mapping[str, Path]) -> None:
    metrics = summary["metrics"]
    print("\n=== REPLAY STATISTICS AUDIT ===")
    print(f"Program version:              {summary['program_version']}")
    print(f"Run ID:                       {summary['run_id']}")
    print(f"Initial equity:               ${metrics['initial_cash']:,.2f}")
    print(f"Final liquid cash:            ${metrics['final_cash']:,.2f}")
    print(f"Final gross equity:           ${metrics['final_gross_equity']:,.2f}")
    print(f"Final net liquidation equity: ${metrics['final_equity']:,.2f}")
    print(f"Total return:                 {metrics['total_return_pct']:.2f}%")
    print(f"Realized P&L:                 ${metrics['realized_pnl']:,.2f}")
    print(f"Unrealized P&L:               ${metrics['unrealized_pnl']:,.2f}")
    print(f"Completed round trips:        {metrics['completed_round_trips']}")
    print(f"Transaction legs:             {metrics['transaction_legs']}")
    print(f"Win rate:                     {metrics['win_rate_pct']:.2f}%")
    profit_factor = metrics["profit_factor"]
    print(
        "Profit factor:                 "
        + (f"{profit_factor:.3f}" if profit_factor is not None else "N/A")
    )
    print(f"Maximum drawdown:             {metrics['max_drawdown_pct']:.2f}%")
    print(f"Annualized Sharpe:            {metrics['sharpe']:.3f}")
    print(f"Candidate entry signals:      {metrics['candidate_entry_signals']}")
    print(f"Skipped entry signals:        {metrics['skipped_entry_signals']}")
    print(f"Ambiguous exit bars:          {metrics['ambiguous_exit_bars']}")
    print(f"Factor mode:                  {summary['factor_mode']}")
    print(f"Intrabar policy:              {summary['intrabar_policy']}")
    print(f"Point-in-time safe:           {summary['point_in_time_safe']}")
    if summary.get("warnings"):
        print("Warnings:")
        for warning in summary["warnings"]:
            print(f"  - {warning}")
    print(f"Artifacts:                    {artifact_paths['run_directory']}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        configure_logging(args.log_level)
        validate_args(args)
        artifacts = run_replay(args)
        artifact_paths = persist_artifacts(
            artifacts, Path(args.output_dir).expanduser().resolve()
        )
        print_summary(artifacts.summary, artifact_paths)

        if args.publish_sheets:
            try:
                push_to_dashboard(
                    artifacts.summary,
                    artifacts.transactions,
                    sheet_id=args.sheet_id,
                    account=args.gog_account,
                    binary=args.gog_binary,
                )
                print("Google Sheets publication completed successfully.")
            except PublishError as exc:
                message = (
                    "Local replay succeeded, but Google Sheets publication failed: "
                    f"{exc}"
                )
                if args.sheet_required:
                    LOGGER.error(message)
                    return 4
                LOGGER.warning(message)
        else:
            print("Google Sheets publication was not requested; no cloud document was modified.")
        return 0
    except ConfigurationError as exc:
        LOGGER.error("Configuration error: %s", exc)
        return 2
    except DataSourceError as exc:
        LOGGER.error("Market-data error: %s", exc)
        return 3
    except ReplayError as exc:
        LOGGER.error("Replay error: %s", exc)
        return 5
    except KeyboardInterrupt:
        LOGGER.error("Interrupted")
        return 130
    except Exception:
        LOGGER.exception("Unexpected internal error")
        return 10


if __name__ == "__main__":
    sys.exit(main())
