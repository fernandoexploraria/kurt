#!/usr/bin/env python3
from __future__ import annotations

import csv
import importlib.util
import json
import math
import tempfile
import unittest
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

UTC = timezone.utc

MODULE_PATH = Path(__file__).with_name("kurt_sandbox_v2.py")
SPEC = importlib.util.spec_from_file_location("kurt_sandbox_v2", MODULE_PATH)
assert SPEC and SPEC.loader
ks = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ks
SPEC.loader.exec_module(ks)


class TimestampAndNormalizationTests(unittest.TestCase):
    def test_parse_iso_string_and_milliseconds(self) -> None:
        expected = datetime(2026, 1, 15, 14, 30, tzinfo=UTC)
        self.assertEqual(ks.parse_timestamp("2026-01-15T14:30:00Z"), expected)
        self.assertEqual(
            ks.parse_timestamp(int(expected.timestamp() * 1000)), expected
        )

    def test_normalize_candles_deduplicates_and_discards_invalid_rows(self) -> None:
        rows = [
            {"date": "2026-01-01", "open": 10, "max": 12, "min": 9, "close": 11},
            {"date": "2026-01-01", "open": 10, "max": 13, "min": 9, "close": 12},
            {"date": "bad", "open": 10, "max": 12, "min": 9, "close": 11},
        ]
        candles, invalid, duplicates = ks.normalize_candles(rows)
        self.assertEqual(len(candles), 1)
        self.assertEqual(candles[0].close, 12)
        self.assertEqual(invalid, 1)
        self.assertEqual(duplicates, 1)


class ExecutionModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.candle = ks.Candle(
            timestamp=datetime(2026, 1, 2, tzinfo=UTC),
            open=100,
            high=110,
            low=90,
            close=102,
        )

    def test_ambiguous_existing_bar_is_conservative_by_default(self) -> None:
        event = ks.resolve_existing_exit(self.candle, 95, 105, "conservative")
        assert event
        self.assertTrue(event.ambiguous)
        self.assertEqual(event.reason, "STOP_AMBIGUOUS")
        self.assertEqual(event.raw_price, 95)

        optimistic = ks.resolve_existing_exit(self.candle, 95, 105, "optimistic")
        assert optimistic
        self.assertEqual(optimistic.reason, "TAKE_PROFIT_AMBIGUOUS")

    def test_intrabar_entry_does_not_receive_unearned_target_under_conservative_policy(self) -> None:
        candle = ks.Candle(
            timestamp=datetime(2026, 1, 2, tzinfo=UTC),
            open=105,
            high=115,
            low=99,
            close=104,
        )
        event = ks.resolve_entry_bar_exit(
            candle,
            entry_limit=100,
            stop_price=95,
            target_price=110,
            intrabar_policy="conservative",
        )
        self.assertIsNone(event)

    def test_stop_ratchets_and_never_moves_down(self) -> None:
        position = ks.Position(
            trade_id="T1",
            ticker="ABC",
            shares=10,
            entry_time=datetime(2026, 1, 1, tzinfo=UTC),
            entry_price=100,
            entry_gross=1000,
            entry_fee=0.5,
            entry_slippage=0,
            entry_outlay=1000.5,
            entry_atr=2,
            trailing_multiplier=3,
            highest_seen=110,
            stop_price=104,
            target_price=130,
            beta=1.0,
        )
        candle = ks.Candle(
            timestamp=datetime(2026, 1, 2, tzinfo=UTC),
            open=108,
            high=112,
            low=105,
            close=106,
        )
        ks.update_position_after_bar(
            position,
            candle,
            current_atr=5,
            loser_leash=False,
            entered_this_bar=False,
            entered_at_open=False,
            intrabar_policy="conservative",
            price_decimals=2,
        )
        self.assertEqual(position.stop_price, 104)

    def test_sizing_never_uses_one_share_override_to_break_cap(self) -> None:
        candidate = ks.EntryCandidate(
            ticker="EXPENSIVE",
            candle_index=20,
            raw_entry_price=5000,
            entry_limit=5000,
            previous_atr=100,
            beta=1.0,
            trailing_multiplier=3,
            target_price=5500,
            initial_stop=4700,
            dea_multiplier=1,
            catalyst_multiplier=1,
            regime_multiplier=1,
            factor_adjustment=0,
            point_in_time_record_used=False,
        )
        shares = ks.calculate_target_shares(
            candidate,
            cash=20_000,
            equity=20_000,
            risk_per_trade=0.01,
            max_position_pct=0.10,
            fee_rate=0.0005,
            slippage_rate=0,
        )
        self.assertEqual(shares, 0)


class PointInTimeTests(unittest.TestCase):
    def test_latest_eligible_record_is_selected_and_future_record_is_excluded(self) -> None:
        cache = {
            "ABC": {
                "snapshots": {
                    "2026-01-01": {"dea_score": 0.2},
                    "2026-02-01": {"dea_score": 0.9},
                }
            }
        }
        record, temporal = ks.resolve_factor_record(
            cache,
            "ABC",
            datetime(2026, 1, 15, tzinfo=UTC),
            "point-in-time",
        )
        self.assertTrue(temporal)
        self.assertEqual(record["dea_score"], 0.2)

    def test_undated_record_is_ignored_unless_static_mode_is_explicit(self) -> None:
        cache = {"ABC": {"dea_score": 0.8}}
        as_of = datetime(2026, 1, 15, tzinfo=UTC)
        record, temporal = ks.resolve_factor_record(cache, "ABC", as_of, "point-in-time")
        self.assertEqual(record, {})
        self.assertFalse(temporal)
        static_record, static_temporal = ks.resolve_factor_record(cache, "ABC", as_of, "static")
        self.assertEqual(static_record["dea_score"], 0.8)
        self.assertFalse(static_temporal)


class StatisticsTests(unittest.TestCase):
    def test_intraday_sharpe_annualization_uses_timeframe(self) -> None:
        self.assertAlmostEqual(ks.periods_per_year("60"), 252 * 6.5)
        self.assertAlmostEqual(ks.periods_per_year("15"), 252 * 26)

    def test_trade_pairing_is_by_trade_id_and_pnl_is_stored_at_exit(self) -> None:
        candidate = ks.EntryCandidate(
            ticker="ABC",
            candle_index=20,
            raw_entry_price=100,
            entry_limit=100,
            previous_atr=2,
            beta=1,
            trailing_multiplier=3,
            target_price=110,
            initial_stop=94,
            dea_multiplier=1,
            catalyst_multiplier=1,
            regime_multiplier=1,
            factor_adjustment=0,
            point_in_time_record_used=False,
        )
        position, buy = ks.execute_buy(
            candidate,
            shares=10,
            timestamp=datetime(2026, 1, 1, tzinfo=UTC),
            fee_rate=0.0005,
            slippage_rate=0,
        )
        proceeds, sell, completed = ks.execute_sell(
            position,
            ks.ExitEvent("TAKE_PROFIT", 110),
            timestamp=datetime(2026, 1, 2, tzinfo=UTC),
            fee_rate=0.0005,
            slippage_rate=0,
        )
        self.assertEqual(buy["trade_id"], sell["trade_id"])
        self.assertEqual(completed["trade_id"], buy["trade_id"])
        self.assertGreater(completed["realized_pnl"], 0)
        self.assertAlmostEqual(proceeds, sell["value"])




class CommandLineCompatibilityTests(unittest.TestCase):
    def test_legacy_boolean_values_and_negative_flags_are_supported(self) -> None:
        parser = ks.build_parser()
        legacy = parser.parse_args(
            [
                "--tickers",
                "ABC",
                "--start-date",
                "2026-01-01",
                "--loser_leash",
                "False",
                "--dpi_bearish",
                "False",
            ]
        )
        self.assertFalse(legacy.loser_leash)
        self.assertFalse(legacy.dpi_bearish)

        negative = parser.parse_args(
            [
                "--tickers",
                "ABC",
                "--start-date",
                "2026-01-01",
                "--no-loser-leash",
                "--no-dpi-bearish",
            ]
        )
        self.assertFalse(negative.loser_leash)
        self.assertFalse(negative.dpi_bearish)


class DateRangeTests(unittest.TestCase):
    def test_replay_end_is_exclusive(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            rows = []
            start = datetime(2026, 1, 1, tzinfo=UTC)
            for index in range(20):
                rows.append(
                    {
                        "date": (start + timedelta(days=index)).date().isoformat(),
                        "open": 100,
                        "max": 101,
                        "min": 99,
                        "close": 100,
                    }
                )
            (root / "ABC.json").write_text(json.dumps(rows), encoding="utf-8")
            series = ks.build_market_series(
                "ABC",
                exchange_cache={},
                data_dir=root,
                api_key=None,
                rapidapi_host="unused",
                timeframe="D",
                range_count=60,
                start=datetime(2026, 1, 15, tzinfo=UTC),
                end_exclusive=datetime(2026, 1, 16, tzinfo=UTC),
                atr_period=3,
                timeout_seconds=1,
                retries=0,
            )
            timestamps = [series.candles[index].timestamp for index in series.sim_indices]
            self.assertEqual(timestamps, [datetime(2026, 1, 15, tzinfo=UTC)])


class MissingBarValuationTests(unittest.TestCase):
    def test_missing_bar_uses_last_known_price_not_a_future_terminal_candle(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "data"
            data_dir.mkdir()

            abc_rows = []
            for day in range(1, 5):
                abc_rows.append(
                    {
                        "date": f"2026-01-0{day}",
                        "open": 100,
                        "max": 101,
                        "min": 99,
                        "close": 100,
                    }
                )
            abc_rows.extend(
                [
                    {"date": "2026-01-05", "open": 100, "max": 101, "min": 97, "close": 99},
                    # Deliberately no ABC bar on 2026-01-06.
                    {"date": "2026-01-07", "open": 99, "max": 101, "min": 98, "close": 100},
                    # Future outlier outside the replay. The original script could use this on 2026-01-06.
                    {"date": "2026-01-20", "open": 1000, "max": 1001, "min": 999, "close": 1000},
                ]
            )
            xyz_rows = []
            for day in range(1, 8):
                xyz_rows.append(
                    {
                        "date": f"2026-01-0{day}",
                        "open": 50,
                        "max": 50.5,
                        "min": 49.5,
                        "close": 50,
                    }
                )
            (data_dir / "ABC.json").write_text(json.dumps(abc_rows), encoding="utf-8")
            (data_dir / "XYZ.json").write_text(json.dumps(xyz_rows), encoding="utf-8")

            parser = ks.build_parser()
            args = parser.parse_args(
                [
                    "--tickers",
                    "ABC,XYZ",
                    "--start-date",
                    "2026-01-05",
                    "--days",
                    "3",
                    "--atr-period",
                    "3",
                    "--data-dir",
                    str(data_dir),
                    "--output-dir",
                    str(root / "out"),
                    "--factor-mode",
                    "off",
                    "--exchange-cache",
                    str(root / "none1.json"),
                    "--shield-file",
                    str(root / "none2.json"),
                    "--optimized-file",
                    str(root / "none3.json"),
                    "--dea-file",
                    str(root / "none4.json"),
                    "--log-level",
                    "ERROR",
                ]
            )
            ks.validate_args(args)
            artifacts = ks.run_replay(args)
            jan6 = next(
                row
                for row in artifacts.equity_curve
                if row["timestamp"].startswith("2026-01-06")
            )
            self.assertLess(jan6["equity"], 25_000)
            self.assertLess(artifacts.summary["metrics"]["final_equity"], 25_000)


class EndToEndLocalReplayTests(unittest.TestCase):
    def test_local_replay_writes_complete_artifacts_without_cloud_side_effects(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "data"
            output_dir = root / "output"
            data_dir.mkdir()

            rows = []
            start = datetime(2025, 12, 1, tzinfo=UTC)
            price = 100.0
            for index in range(80):
                open_price = price
                close = open_price + 0.2
                high = max(open_price, close) + 2.0
                low = min(open_price, close) - 2.5
                if index in {50, 65}:
                    low = open_price - 8.0
                    high = open_price + 7.0
                    close = open_price + 1.0
                if index in {54, 69}:
                    high = open_price + 12.0
                    low = open_price - 1.0
                    close = open_price + 8.0
                rows.append(
                    {
                        "date": (start + timedelta(days=index)).date().isoformat(),
                        "open": round(open_price, 2),
                        "max": round(high, 2),
                        "min": round(low, 2),
                        "close": round(close, 2),
                    }
                )
                price = close
            (data_dir / "ABC.json").write_text(json.dumps(rows), encoding="utf-8")

            parser = ks.build_parser()
            args = parser.parse_args(
                [
                    "--tickers",
                    "ABC",
                    "--start-date",
                    "2026-01-15",
                    "--days",
                    "25",
                    "--data-dir",
                    str(data_dir),
                    "--output-dir",
                    str(output_dir),
                    "--exchange-cache",
                    str(root / "none1.json"),
                    "--shield-file",
                    str(root / "none2.json"),
                    "--optimized-file",
                    str(root / "none3.json"),
                    "--dea-file",
                    str(root / "none4.json"),
                    "--log-level",
                    "ERROR",
                ]
            )
            ks.validate_args(args)
            artifacts = ks.run_replay(args)
            paths = ks.persist_artifacts(artifacts, output_dir)

            self.assertTrue(paths["summary"].exists())
            self.assertTrue(paths["transactions"].exists())
            self.assertTrue(paths["completed_trades"].exists())
            self.assertTrue(paths["equity_curve"].exists())
            self.assertFalse(args.publish_sheets)
            self.assertEqual(
                artifacts.summary["metrics"]["completed_round_trips"],
                len(artifacts.completed_trades),
            )
            ids = [trade["trade_id"] for trade in artifacts.completed_trades]
            self.assertEqual(len(ids), len(set(ids)))


class FinalHardeningTests(unittest.TestCase):
    def test_cli_aliases_are_normalized(self) -> None:
        parser = ks.build_parser()
        args = parser.parse_args(
            [
                "--tickers",
                "ABC",
                "--start_date",
                "2026-01-01",
                "--factor_mode",
                "neutral",
                "--same_bar_policy",
                "stop_first",
                "--max_portfolio_risk_pct",
                "0.05",
                "--max_positions",
                "4",
                "--data_dir",
                ".",
                "--output_dir",
                "./out",
            ]
        )
        self.assertEqual(args.factor_mode, "off")
        self.assertEqual(args.intrabar_policy, "conservative")
        self.assertEqual(args.max_portfolio_risk_pct, 0.05)
        self.assertEqual(args.max_positions, 4)

    def test_snapshot_directory_supports_both_documented_layouts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "quiver_shield_2026-01-15.json").write_text(
                json.dumps({"ABC": {"score": 61}}), encoding="utf-8"
            )
            dated = root / "2026-01-16"
            dated.mkdir()
            (dated / "optimized_entries.json").write_text(
                json.dumps({"ABC": {"beta": 1.2}}), encoding="utf-8"
            )
            caches, count = ks.load_factor_snapshot_directory(root)
            self.assertEqual(count, 2)
            shield, temporal = ks.resolve_factor_record(
                caches["shield"],
                "ABC",
                datetime(2026, 1, 15, 23, 59, tzinfo=UTC),
                "point-in-time",
            )
            optimized, optimized_temporal = ks.resolve_factor_record(
                caches["optimized"],
                "ABC",
                datetime(2026, 1, 16, 23, 59, tzinfo=UTC),
                "point-in-time",
            )
            self.assertTrue(temporal)
            self.assertTrue(optimized_temporal)
            self.assertEqual(shield["score"], 61)
            self.assertEqual(optimized["beta"], 1.2)

    def test_portfolio_risk_budget_caps_position_size(self) -> None:
        candidate = ks.EntryCandidate(
            ticker="ABC",
            candle_index=20,
            raw_entry_price=100,
            entry_limit=100,
            previous_atr=2,
            beta=1,
            trailing_multiplier=3,
            target_price=110,
            initial_stop=90,
            dea_multiplier=1,
            catalyst_multiplier=1,
            regime_multiplier=1,
            factor_adjustment=0,
            point_in_time_record_used=False,
        )
        shares = ks.calculate_target_shares(
            candidate,
            cash=10_000,
            equity=10_000,
            risk_per_trade=1.0,
            max_position_pct=1.0,
            fee_rate=0.0,
            slippage_rate=0.0,
            max_risk_dollars=25.0,
        )
        self.assertEqual(shares, 2)

    def test_same_bar_exit_cash_is_not_reused_unless_requested(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "data"
            data_dir.mkdir()
            dates = [f"2026-01-0{day}" for day in range(1, 7)]
            stable = lambda date: {
                "date": date,
                "open": 100,
                "max": 101,
                "min": 99,
                "close": 100,
            }
            a_rows = [stable(date) for date in dates]
            b_rows = [stable(date) for date in dates]
            # A enters on day 4 and exits at its target on day 5.
            a_rows[3] = {"date": dates[3], "open": 100, "max": 101, "min": 97, "close": 99}
            a_rows[4] = {"date": dates[4], "open": 100, "max": 106, "min": 99, "close": 105}
            # B first becomes an entry candidate on the same bar as A's exit.
            b_rows[4] = {"date": dates[4], "open": 100, "max": 101, "min": 97, "close": 99}
            (data_dir / "A.json").write_text(json.dumps(a_rows), encoding="utf-8")
            (data_dir / "B.json").write_text(json.dumps(b_rows), encoding="utf-8")

            def replay(reuse: bool) -> ks.ReplayArtifacts:
                parser = ks.build_parser()
                arguments = [
                    "--tickers",
                    "A,B",
                    "--start-date",
                    "2026-01-04",
                    "--days",
                    "2",
                    "--initial-cash",
                    "1000",
                    "--atr-period",
                    "2",
                    "--risk-per-trade",
                    "1",
                    "--max-position-pct",
                    "1",
                    "--max-portfolio-risk-pct",
                    "1",
                    "--max-positions",
                    "10",
                    "--commission-bps",
                    "0",
                    "--data-dir",
                    str(data_dir),
                    "--factor-mode",
                    "off",
                    "--exchange-cache",
                    str(root / "none1.json"),
                    "--shield-file",
                    str(root / "none2.json"),
                    "--optimized-file",
                    str(root / "none3.json"),
                    "--dea-file",
                    str(root / "none4.json"),
                    "--log-level",
                    "ERROR",
                ]
                if reuse:
                    arguments.append("--reuse-same-bar-exit-cash")
                args = parser.parse_args(arguments)
                ks.validate_args(args)
                return ks.run_replay(args)

            conservative = replay(False)
            permissive = replay(True)
            conservative_buys = [
                tx for tx in conservative.transactions
                if tx["ticker"] == "B" and tx["action"] == "BUY"
            ]
            permissive_buys = [
                tx for tx in permissive.transactions
                if tx["ticker"] == "B" and tx["action"] == "BUY"
            ]
            self.assertEqual(conservative_buys, [])
            self.assertEqual(len(permissive_buys), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
