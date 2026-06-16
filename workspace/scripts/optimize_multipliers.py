#!/usr/bin/env python3
import yfinance as yf
import pandas as pd
import json
import os
import subprocess
import math
import tempfile
from datetime import datetime

MULTIPLIERS_FILE = "/root/.openclaw/workspace/memory/optimized_multipliers.json"
ENTRIES_FILE = "/root/.openclaw/workspace/memory/optimized_entries.json"
LIVE_SHEET_ID = "1kjzfc6uEzBFtmNjlU1x3TVbHuWPgY7jnNce8mNTe66I"
ACCOUNT = "fernando@exploraria.ai"

# --- CONFIGURATION: TRANSACTION FEES & SLIPPAGE ---
# 5 bps (0.05%) per side = 10 bps (0.10%) round-trip fee-and-slippage penalty
COST_PER_SIDE = 0.0005

def run_gog(command):
    full_cmd = ["gog", "sheets"] + command
    env = {**os.environ, "GOG_ACCOUNT": ACCOUNT}
    result = subprocess.run(full_cmd, shell=False, capture_output=True, text=True, env=env)
    if result.returncode == 0:
        try:
            return json.loads(result.stdout)
        except:
            return None
    return None

def get_master_universe():
    print("Pulling universe from Google Sheets (Positions + Watchlist)...")
    tickers = set()

    # Get Positions
    pos_data = run_gog(["get", LIVE_SHEET_ID, "Positions!A:A", "--json"])
    if pos_data and "values" in pos_data:
        for row in pos_data["values"]:
            if row and row[0].strip() and row[0].strip() != "CASH" and row[0].strip().upper() != "TICKER":
                tickers.add(row[0].strip())

    # Get Watchlist
    watch_data = run_gog(["get", LIVE_SHEET_ID, "Watchlist!A:A", "--json"])
    if watch_data and "values" in watch_data:
        for row in watch_data["values"]:
            if row and row[0].strip() and row[0].strip().upper() != "TICKER":
                tickers.add(row[0].strip())

    return list(tickers)

def calculate_rsi(data, periods=14):
    close_delta = data['Close'].diff()
    up = close_delta.clip(lower=0)
    down = -1 * close_delta.clip(upper=0)
    ma_up = up.ewm(com=periods - 1, adjust=False).mean()
    ma_down = down.ewm(com=periods - 1, adjust=False).mean()
    rsi = ma_up / ma_down
    rsi = 100 - (100/(1 + rsi))
    return rsi

def save_json_atomic(data, path):
    """
    UPGRADE P0-6: Writes state files atomically using temporary files
    and os.replace to prevent file truncation during mid-write crashes [P0-6].
    """
    dir_name = os.path.dirname(path)
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)

    with tempfile.NamedTemporaryFile('w', dir=dir_name, delete=False) as tf:
        temp_path = tf.name
        json.dump(data, tf, indent=2)
        tf.flush()
        os.fsync(tf.fileno())

    os.chmod(temp_path, 0o644)
    os.replace(temp_path, path)

def backtest_strategy(df, m, strategy_name):
    """
    UPGRADE P1-6: Aligns backtest entry logic with active resting limit traps [P1-6].
    Simulates entering positions strictly via technical limit order wicks rather than closing price.
    """
    in_position = False
    trap_armed = False
    wait_for_reset = False
    entry_price = 0.0
    highest_seen = 0.0
    total_compound_return = 1.0
    trades = 0
    wins = 0

    for index, row in df.iterrows():
        close = float(row.Close)
        open_price = float(row.Open)
        high = float(row.High)
        low = float(row.Low)
        atr = float(row.ATR)
        ema_50 = float(row.EMA_50)
        ema_200 = float(row.EMA_200)
        rsi = float(row.RSI)
        s1_val = float(row.S1)
        s2_val = float(row.S2)

        if pd.isna(atr) or pd.isna(ema_200) or pd.isna(rsi) or pd.isna(s1_val) or pd.isna(s2_val):
            continue

        if in_position:
            # We are in a trade. Evaluate trailing stop exits using today's price action.
            highest_seen = max(highest_seen, high)
            trailing_floor = highest_seen - (atr * m)

            if low < trailing_floor:
                in_position = False
                wait_for_reset = True
                exit_price = open_price if open_price < trailing_floor else trailing_floor

                # Deduct round-trip friction costs [P1-5]
                trade_return = ((exit_price - entry_price) / entry_price) - (2 * COST_PER_SIDE) if entry_price > 0 else 0
                total_compound_return *= (1 + trade_return)

                # A trade is counted as a "win" ONLY if it is net-profitable after fees
                if trade_return > 0: wins += 1
        else:
            # We are NOT in a trade.
            # First, check if a trap was armed yesterday, and if we get filled today (T+1) [P1-6]
            if trap_armed and not wait_for_reset:
                if strategy_name == "EMA_50_Bounce":
                    limit_price = ema_50
                elif strategy_name in ["EMA_200_Bounce", "Holy_Grail"]:
                    limit_price = ema_200
                elif strategy_name == "RSI_40":
                    limit_price = s1_val
                elif strategy_name == "RSI_30":
                    limit_price = s2_val
                else:
                    limit_price = close

                # Check if the low of today touched or breached our resting limit price [P1-6]
                if low <= limit_price and limit_price > 0:
                    in_position = True
                    # If the market opened below our limit, we get filled at the Open (slippage protection) [P1-6]
                    entry_price = open_price if open_price < limit_price else limit_price
                    highest_seen = high
                    trades += 1
                    trap_armed = False # Trap executed

            # Second, evaluate today's close to see if we should arm (or keep armed) a trap for tomorrow
            if not in_position:
                triggered = False
                if strategy_name == "RSI_30" and rsi < 30:
                    triggered = True
                elif strategy_name == "RSI_40" and rsi < 40:
                    triggered = True
                elif strategy_name == "EMA_50_Bounce" and low <= ema_50 and close >= (ema_50 * 0.98):
                    triggered = True
                elif strategy_name == "EMA_200_Bounce" and low <= ema_200 and close >= (ema_200 * 0.98):
                    triggered = True
                elif strategy_name == "Holy_Grail" and close > ema_200 and rsi < 40:
                    triggered = True

                if triggered and not wait_for_reset:
                    trap_armed = True
                else:
                    wait_for_reset = False
                    trap_armed = False

    if in_position:
        end_price = float(df.Close.iloc[-1])

        # Deduct final liquidation friction costs [P1-5]
        trade_return = ((end_price - entry_price) / entry_price) - (2 * COST_PER_SIDE) if entry_price > 0 else 0
        total_compound_return *= (1 + trade_return)
        if trade_return > 0: wins += 1

    final_return = (total_compound_return - 1) * 100
    win_rate = (wins / trades * 100) if trades > 0 else 0.0

    return final_return, win_rate, trades

# --- Rolling Out-of-Sample Walk-Forward Backtester ---
def walk_forward_backtest(df, m, strategy_name, w_fit, rho):
    """
    Simulates a continuous, chronological Walk-Forward Optimization pipeline.
    Re-optimizes parameters on In-Sample (IS) slices and evaluates strictly Out-of-Sample (OOS).
    """
    w_val = int(w_fit / rho)
    total_len = len(df)

    if total_len < (w_fit + w_val):
        return -999.0, 0 # Insufficient data points for this specific temporal setup

    global_oos_returns = 1.0
    total_trades = 0
    step = w_val
    start_idx = 0

    # Chronologically roll both training and validation windows forward
    while (start_idx + w_fit + w_val) <= total_len:
        is_slice = df.iloc[start_idx : start_idx + w_fit]
        oos_slice = df.iloc[start_idx + w_fit : start_idx + w_fit + w_val]

        # 1. Evaluate strategy suitability on the rolling In-Sample (IS) window
        is_return, is_win, is_trades = backtest_strategy(is_slice, m, strategy_name)

        # 2. If valid, execute parameters on the unseen contiguous Out-of-Sample (OOS) window
        if is_trades > 0:
            oos_return, oos_win, oos_trades = backtest_strategy(oos_slice, m, strategy_name)
            global_oos_returns *= (1.0 + (oos_return / 100.0))
            total_trades += oos_trades

        # Walk window forward by the validation step size
        start_idx += step

    final_oos_return = (global_oos_returns - 1.0) * 100.0
    return final_oos_return, total_trades

def load_existing_json(path):
    if os.path.exists(path):
        try:
            with open(path, 'r') as f:
                return json.load(f)
        except: pass
    return {}

def main():
    import sys
    print(f"[{datetime.now().isoformat()}] Starting Weekly Joint Quant Optimizer (Worker Node)...")

    # If tickers are passed via CLI, use those. Otherwise, fetch the full universe.
    cli_args = sys.argv[1:]
    if cli_args:
        universe = cli_args
        print(f"Worker received {len(universe)} specific tickers to process.")
    else:
        universe = get_master_universe()
        if not universe:
            print("Failed to pull universe. Exiting.")
            return 1
        print(f"Found {len(universe)} unique tickers to optimize.\n")

    # Parameters and strategies to test
    multipliers_to_test = [1.5, 2.0, 2.5, 3.0, 3.5, 4.0]
    strategies = ["RSI_30", "RSI_40", "EMA_50_Bounce", "EMA_200_Bounce", "Holy_Grail"]

    # --- TEMPORAL PARAMETER GRID ---
    w_fit_grid = [126, 252, 378, 504] # 6m, 12m, 18m, 24m training windows
    rho_grid = [3, 4, 5, 6] # Corrected validation ratios (Minimum 3:1 up to 8:1) [P1-4]

    # Lightweight subset to ensure extremely fast Sunday execution
    grid_strats = ["RSI_40", "EMA_200_Bounce", "EMA_50_Bounce"]
    grid_mults = [1.5, 2.0, 2.5, 3.0, 3.5, 4.0]

    # LOAD EXISTING DATA SO WE APPEND INSTEAD OF OVERWRITE
    multipliers_results = load_existing_json(MULTIPLIERS_FILE)
    entries_results = load_existing_json(ENTRIES_FILE)

    for ticker in universe:
        print(f"Optimizing {ticker}...", end=" ", flush=True)
        try:
            yf_ticker = ticker.replace(".", "-")
            df = yf.download(yf_ticker, period="5y", progress=False)
            if df.empty:
                print("Failed (No Data)")
                continue

            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            # --- DYNAMIC CAUSAL MONTHLY PIVOT RESAMPLER [P1-6] ---
            # 1. Create a YearMonth period column on daily data for clean alignment [P1-6]
            df['YearMonth'] = df.index.to_period('M')

            # 2. Resample to monthly period to calculate monthly support S1/S2 pivots
            df_monthly = df.resample('ME').agg({'High': 'max', 'Low': 'min', 'Close': 'last'})
            df_monthly.index = df_monthly.index.to_period('M')

            df_monthly['PP'] = (df_monthly.High + df_monthly.Low + df_monthly.Close) / 3.0
            df_monthly['S1'] = (2.0 * df_monthly.PP) - df_monthly.High
            df_monthly['S2'] = df_monthly.PP - (df_monthly.High - df_monthly.Low)

            # 3. Shift the monthly pivots by 1 period to ensure strict look-ahead causality [P1-6]
            # (e.g., February daily rows will use January's calculated monthly pivots)
            df_monthly_shifted = df_monthly[['S1', 'S2']].shift(1)

            # 4. Join the shifted monthly pivots back to the daily data using the YearMonth index [P1-6]
            df = df.join(df_monthly_shifted, on='YearMonth', rsuffix='_monthly')

            df['Prev_Close'] = df['Close'].shift(1)
            tr1 = df['High'] - df['Low']
            tr2 = abs(df['High'] - df['Prev_Close'])
            tr3 = abs(df['Low'] - df['Prev_Close'])
            df['ATR'] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
            df['ATR'] = df['ATR'].rolling(window=14).mean()
            df['EMA_50'] = df['Close'].ewm(span=50, adjust=False).mean()
            df['EMA_200'] = df['Close'].ewm(span=200, adjust=False).mean()
            df['RSI'] = calculate_rsi(df)

            # Purge NaN warm-up window before grid search
            df = df.dropna()

            # 1. GRID SEARCH OPTIMAL TEMPORAL WINDOWS
            best_w_fit = 252
            best_rho = 3
            best_wfo_score = -999.0

            for w_fit in w_fit_grid:
                for rho in rho_grid:
                    w_val = int(w_fit / rho)
                    if len(df) < (w_fit + w_val):
                        continue

                    # Run walk-forward simulation across representative parameters
                    for strat in grid_strats:
                        for m_val in grid_mults:
                            oos_ret, oos_trades = walk_forward_backtest(df, m_val, strat, w_fit, rho)
                            if oos_ret > best_wfo_score and oos_trades >= 2:
                                best_wfo_score = oos_ret
                                best_w_fit = w_fit
                                best_rho = rho

            # 2. RUN JOINT OPTIMIZATION ON IDENTIFIED ROBUST BOUNDARIES [P1-1]
            best_w_val = int(best_w_fit / best_rho)
            if best_w_val == 0:
                best_w_val = 1
            df_train = df.iloc[:-best_w_val]
            df_val = df.iloc[-best_w_val:]

            candidates = []
            for strat in strategies:
                for m in multipliers_to_test:
                    t_ret, t_win, t_trades = backtest_strategy(df_train, m, strat)
                    if t_trades > 0:
                        candidates.append((strat, m, t_ret))

            candidates.sort(key=lambda x: x[2], reverse=True)

            robust_candidate = None
            for strat, m, t_ret in candidates:
                v_ret, v_win, v_trades = backtest_strategy(df_val, m, strat)
                if v_ret > 0: # Must survive validation out-of-sample
                    robust_candidate = (strat, m, v_ret, v_win, v_trades)
                    break

            if robust_candidate:
                best_strat, best_m, val_ret, val_win, val_trades = robust_candidate
            else:
                # If nothing survived validation, fall back to purely defensive default
                best_strat, best_m, val_ret, val_win, val_trades = "None", 3.0, 0.0, 0.0, 0

            best_return = -999.0
            if best_strat != "None":
                best_return, _, _ = backtest_strategy(df, best_m, best_strat)

            # Store results for both files [P1-1]
            multipliers_results[ticker] = best_m
            entries_results[ticker] = {
                "best_trigger": best_strat if best_strat != "None" else None,
                "exit_multiplier_used": best_m,
                "win_rate_pct": round(val_win, 2),
                "total_return_pct": round(val_ret, 2),
                "trades_executed": val_trades
            }

            print(f"Window: IS {best_w_fit}d/OOS {best_w_val}d | Best: {best_m}x with {best_strat} ({best_return:.2f}%)")

        except Exception as e:
            print(f"Failed ({e})")
            multipliers_results[ticker] = 3.0
            entries_results[ticker] = {
                "best_trigger": None,
                "exit_multiplier_used": 3.0,
                "win_rate_pct": 0.0,
                "total_return_pct": 0.0,
                "trades_executed": 0
            }

    # Save BOTH output files atomically [P1-1, P0-6]
    try:
        save_json_atomic(multipliers_results, MULTIPLIERS_FILE)
        save_json_atomic(entries_results, ENTRIES_FILE)
        print(f"\nJoint Optimization complete. Saved files atomically.")
        return 0
    except Exception as e:
        print(f"\n🚨 FATAL: Atomic write failed: {e}")
        return 1

if __name__ == "__main__":
    import sys
    sys.exit(main())
