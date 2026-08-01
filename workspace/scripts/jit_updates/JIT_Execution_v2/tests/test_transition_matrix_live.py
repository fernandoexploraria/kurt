from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import transition_matrix_live as tm  # noqa: E402


class TransitionMathTests(unittest.TestCase):
    def test_jsd_identity_symmetry_and_bounds(self) -> None:
        p = np.array([0.1, 0.2, 0.7])
        q = np.array([0.7, 0.2, 0.1])
        self.assertAlmostEqual(tm.jensen_shannon_divergence(p, p), 0.0, places=12)
        self.assertAlmostEqual(
            tm.jensen_shannon_divergence(p, q),
            tm.jensen_shannon_divergence(q, p),
            places=12,
        )
        self.assertGreaterEqual(tm.jensen_shannon_divergence(p, q), 0.0)
        self.assertLessEqual(tm.jensen_shannon_divergence(p, q), 1.0)

    def test_transition_counts_do_not_cross_sessions_or_gaps(self) -> None:
        index = pd.DatetimeIndex(
            [
                "2026-07-20T13:31:00Z",
                "2026-07-20T13:32:00Z",
                "2026-07-20T13:34:00Z",  # one-minute gap before this row
                "2026-07-21T13:31:00Z",
                "2026-07-21T13:32:00Z",
            ]
        )
        frame = pd.DataFrame(
            {
                "session": [
                    "2026-07-20",
                    "2026-07-20",
                    "2026-07-20",
                    "2026-07-21",
                    "2026-07-21",
                ]
            },
            index=index,
        )
        states = np.array([1, 2, 3, 2, 1], dtype=np.int16)
        stats = tm.estimate_transition_stats(frame, states, 3)

        # Valid transitions are 1->2 on day 1 and 2->1 on day 2 only.
        self.assertEqual(stats.valid_transition_count, 2)
        self.assertEqual(stats.counts[0, 1], 1)
        self.assertEqual(stats.counts[1, 0], 1)
        self.assertEqual(int(stats.counts.sum()), 2)

    def test_occupancy_uses_origin_states_only(self) -> None:
        index = pd.date_range(
            "2026-07-20T13:31:00Z", periods=3, freq="1min"
        )
        frame = pd.DataFrame(
            {"session": ["2026-07-20"] * 3}, index=index
        )
        states = np.array([1, 2, 3], dtype=np.int16)
        stats = tm.estimate_transition_stats(frame, states, 3)
        np.testing.assert_array_equal(stats.origin_counts, np.array([1, 1, 0]))

    def test_adaptive_state_count_reduces_for_duplicate_quantiles(self) -> None:
        values = np.array([0.0] * 80 + [-1.0] * 10 + [1.0] * 10)
        discretizer = tm.StateDiscretizer(
            requested_states=8, min_state_samples=5
        )
        with self.assertRaises(tm.InsufficientDataError):
            discretizer.fit(values)

        richer = np.repeat(np.arange(5, dtype=float), 20)
        discretizer = tm.StateDiscretizer(
            requested_states=8, min_state_samples=5
        )
        discretizer.fit(richer)
        self.assertLess(discretizer.effective_states, 8)
        self.assertGreaterEqual(discretizer.effective_states, 3)


class PolicyTests(unittest.TestCase):
    def _calibration(self) -> dict:
        return {
            "composite_anomaly_percentile": 100.0,
            "sample_count": 10,
        }

    def test_extreme_downside_blocks_new_long(self) -> None:
        config = tm.AnalysisConfig(
            ticker="TEST",
            action=tm.Action.NEW_LONG,
            min_calibration_sessions=4,
        )
        decision, code, _ = tm.make_decision(
            config=config,
            calibration=self._calibration(),
            direction={"downside_evidence": True, "upside_evidence": False},
        )
        self.assertEqual(decision, tm.Decision.BLOCK_NEW_RISK)
        self.assertEqual(code, tm.ExitCode.BLOCK_NEW_RISK)

    def test_model_uncertainty_does_not_block_stop_loss(self) -> None:
        config = tm.AnalysisConfig(
            ticker="TEST",
            action=tm.Action.STOP_LOSS,
            min_calibration_sessions=4,
        )
        decision, code, _ = tm.make_decision(
            config=config,
            calibration=self._calibration(),
            direction={"downside_evidence": True, "upside_evidence": False},
        )
        self.assertEqual(decision, tm.Decision.ALLOW_RISK_REDUCTION)
        self.assertEqual(code, tm.ExitCode.OK)


class InputValidationTests(unittest.TestCase):
    def test_symbol_mismatch_is_rejected(self) -> None:
        frame = pd.DataFrame(
            {
                "ts_event": ["2026-07-20T13:30:00Z"],
                "symbol": ["OTHER"],
                "close": [100.0],
            }
        )
        with self.assertRaises(tm.DataSourceError):
            tm.canonicalize_market_frame(frame, ticker="TEST")

    def test_invalid_timestamps_are_removed(self) -> None:
        frame = pd.DataFrame(
            {
                "timestamp": ["bad", "2026-07-20T13:30:00Z"],
                "close": [99.0, 100.0],
            }
        )
        result = tm.canonicalize_market_frame(frame, ticker="TEST")
        self.assertEqual(len(result), 1)
        self.assertEqual(float(result["close"].iloc[0]), 100.0)

    def test_market_closed_has_dedicated_status(self) -> None:
        frame = pd.DataFrame(
            {"Close": [100.0, 100.1]},
            index=pd.date_range(
                "2026-07-17T13:30:00Z", periods=2, freq="1min"
            ),
        )
        canonical = tm.canonicalize_market_frame(frame, ticker="TEST")
        config = tm.AnalysisConfig(ticker="TEST", mode=tm.RunMode.LIVE)
        with self.assertRaises(tm.MarketClosedError):
            tm.clean_and_sessionize_bars(
                canonical,
                config=config,
                as_of=pd.Timestamp("2026-07-18T15:00:00Z"),
            )

    def test_stale_live_data_has_dedicated_status(self) -> None:
        frame = pd.DataFrame(
            {"Close": [100.0, 100.1, 100.2]},
            index=pd.date_range(
                "2026-07-20T13:30:00Z", periods=3, freq="1min"
            ),
        )
        canonical = tm.canonicalize_market_frame(frame, ticker="TEST")
        config = tm.AnalysisConfig(
            ticker="TEST", mode=tm.RunMode.LIVE, stale_after_minutes=3.0
        )
        with self.assertRaises(tm.StaleDataError):
            tm.clean_and_sessionize_bars(
                canonical,
                config=config,
                as_of=pd.Timestamp("2026-07-20T14:30:00Z"),
            )


class EndToEndTests(unittest.TestCase):
    @staticmethod
    def synthetic_bars(
        session_count: int = 7,
        bars_per_session: int = 140,
        seed: int = 123,
    ) -> pd.DataFrame:
        calendar = tm._get_calendar("XNYS")
        sessions = calendar.sessions_in_range("2026-07-10", "2026-07-23")[
            :session_count
        ]
        rng = np.random.default_rng(seed)
        frames = []
        price = 100.0
        for session_number, session in enumerate(sessions):
            open_time = calendar.session_open(session)
            index = pd.date_range(
                open_time, periods=bars_per_session, freq="1min", tz="UTC"
            )
            # A mild, repeatable intraday volatility curve with enough unique values.
            minute = np.arange(bars_per_session)
            scale = 0.00035 + 0.00015 * np.exp(-minute / 35.0)
            returns = rng.normal(0.0, scale)
            returns += 0.00001 * np.sin((minute + session_number) / 9.0)
            prices = price * np.exp(np.cumsum(returns))
            price = float(prices[-1])
            frames.append(
                pd.DataFrame(
                    {"Close": prices, "Volume": rng.integers(100, 5000, bars_per_session)},
                    index=index,
                )
            )
        return pd.concat(frames)

    def test_full_replay_analysis_returns_structured_result(self) -> None:
        frame = self.synthetic_bars()
        as_of = pd.Timestamp(frame.index.max()) + pd.Timedelta(minutes=1, seconds=2)
        config = tm.AnalysisConfig(
            ticker="TEST",
            action=tm.Action.DIAGNOSTIC,
            mode=tm.RunMode.REPLAY,
            requested_states=8,
            window_minutes=60,
            baseline_sessions=6,
            min_baseline_sessions=4,
            min_live_transitions=20,
            posterior_samples=50,
            top_transitions=3,
        )
        result = tm.analyze_market_frame(
            frame,
            source_metadata={"provider": "synthetic-test"},
            config=config,
            as_of=as_of,
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["schema_version"], tm.SCHEMA_VERSION)
        self.assertGreaterEqual(result["window"]["baseline_transitions"], 200)
        self.assertGreaterEqual(result["window"]["live_transitions"], 50)
        self.assertIn(result["decision"], {"PASS", "CAUTION"})
        self.assertEqual(len(result["top_transition_contributors"]), 3)
        self.assertIn("joint_transition_jsd", result["metrics"])
        self.assertIn("sequence_surprise_bits_per_transition", result["metrics"])

    def test_sessionized_returns_exclude_overnight_gap(self) -> None:
        calendar = tm._get_calendar("XNYS")
        sessions = calendar.sessions_in_range("2026-07-20", "2026-07-21")
        rows = []
        for day_number, session in enumerate(sessions):
            open_time = calendar.session_open(session)
            index = pd.date_range(open_time, periods=4, freq="1min", tz="UTC")
            # Large overnight level jump should not appear as a one-minute return.
            prices = np.array([100, 101, 102, 103], dtype=float) + day_number * 100
            rows.append(pd.DataFrame({"Close": prices}, index=index))
        frame = pd.concat(rows)
        canonical = tm.canonicalize_market_frame(frame, ticker="TEST")
        config = tm.AnalysisConfig(
            ticker="TEST",
            mode=tm.RunMode.REPLAY,
            min_baseline_sessions=1,
            baseline_sessions=1,
            min_live_transitions=2,
        )
        as_of = pd.Timestamp(frame.index.max()) + pd.Timedelta(minutes=1, seconds=2)
        _, returns, quality = tm.clean_and_sessionize_bars(
            canonical, config=config, as_of=as_of
        )
        self.assertEqual(len(returns), 6)  # three returns per session, no overnight return
        self.assertLess(float(returns["log_return"].abs().max()), 0.02)
        self.assertEqual(quality.valid_return_rows, 6)


if __name__ == "__main__":
    unittest.main(verbosity=2)
