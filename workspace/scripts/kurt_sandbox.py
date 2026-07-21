#!/usr/bin/env python3
import json
import subprocess
import os
import tempfile
from datetime import datetime, timedelta
import time
import requests
import math
import sys
import argparse
import pandas as pd
import numpy as np

# --- CONFIGURATION & ISOLATION BOUNDARIES ---
SANDBOX_DIR = "/root/.openclaw/workspace/memory/sandbox"
REPLAY_ORDERS_FILE = os.path.join(SANDBOX_DIR, "replay_pending_orders.json")
REPLAY_RADAR_FILE = os.path.join(SANDBOX_DIR, "replay_trailing_radar.json")
REPLAY_QUEUE_FILE = os.path.join(SANDBOX_DIR, "replay_execution_queue.json")
REPLAY_POSITIONS_FILE = os.path.join(SANDBOX_DIR, "replay_positions.json")
REPLAY_TRANSACTIONS_FILE = os.path.join(SANDBOX_DIR, "replay_transactions.csv")

LIVE_SHEET_ID = "1kjzfc6uEzBFtmNjlU1x3TVbHuWPgY7jnNce8mNTe66I"
ACCOUNT = "fernando@exploraria.ai"
RAPIDAPI_HOST = "tradingview-data1.p.rapidapi.com"

# Shared Production State Files (READ-ONLY to prevent live state corruption)
SHIELD_FILE = "/root/.openclaw/workspace/memory/quiver_shield.json"
CACHE_FILE = "/root/.openclaw/workspace/memory/exchange_cache.json"
OPTIMIZED_FILE = "/root/.openclaw/workspace/memory/optimized_entries.json"
DEA_SCORES_FILE = "/root/.openclaw/workspace/memory/dea_scores.json"

# Simulation Friction Settings
COST_PER_SIDE = 0.0005  # 5 bps (0.05%) per side = 10 bps round-trip transaction drag

# Loser Leash and Sizing Constants
BETA_THRESHOLD = 1.05
DEFAULT_ATR_MULTIPLIER = 3.0
LOSER_LEASH_MIN_PCT = 0.05
LOSER_LEASH_MAX_PCT = 0.08
LOSER_LEASH_ATR_FACTOR = 1.5
LOW_BETA_MULTIPLIER = 4.0

def load_json(path):
    if os.path.exists(path):
        try:
            with open(path, 'r') as f:
                return json.load(f)
        except: pass
    return dict()

def save_json_atomic(data, path):
    """Writes state files atomically to prevent corruption during mid-run crashes."""
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

def run_gog(args_list):
    """Executes 'gog sheets' securely with an isolated environment map (shell=False)."""
    env = os.environ.copy()
    env = ACCOUNT

    cmd_list = ["gog", "sheets"] + args_list
    result = subprocess.run(cmd_list, env=env, shell=False, capture_output=True, text=True)
    if result.returncode!= 0:
        return None
    try:
        return json.loads(result.stdout.strip())
    except:
        return None

def fetch_historical_prices(symbol, timeframe, range_count, api_key):
    """Fetches high-resolution historical candlesticks from TradingView RapidAPI."""
    url = f"https://{RAPIDAPI_HOST}/api/price/{symbol}?timeframe={timeframe}&range={range_count}"
    headers = {
        "x-rapidapi-key": api_key,
        "x-rapidapi-host": RAPIDAPI_HOST
    }
    try:
        res = requests.get(url, headers=headers, timeout=15).json()
        if res.get("success") and "data" in res and "history" in res["data"]:
            return res["data"]["history"]
        elif "history" in res:
            return res["history"]
    except Exception as e:
        print(f"  [!] Error fetching historical prices for {symbol}: {e}")
    return None

def calculate_atr(candles, current_idx):
    """Computes a dynamic 14-period Average True Range (ATR) on the fly."""
    if current_idx < 14:
        return 1.0
    
    trs = list()
    start_idx = current_idx - 13
    if start_idx < 1:
        start_idx = 1
        
    for j in range(start_idx, current_idx + 1):
        h = float(candles[j]["max"])
        l = float(candles[j]["min"])
        pc = float(candles[j-1]["close"])
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)
        
    if not trs:
        return 1.0
    return sum(trs) / len(trs)

def get_quiver_adjustments(ticker, shield_cache, dpi_bearish):
    """Applies Quiver Quant congressional and Dark Pool modifiers to target pricing."""
    modifier = 1.0
    shield_data = shield_cache.get(ticker, {})

    # Dark Pool Index (DPI) Adjustments
    latest_dpi = shield_data.get("dpi", 0.5)
    if latest_dpi > 0.50:
        reduction = (latest_dpi - 0.50) * 0.2
        shift = min(reduction, 0.05)
        modifier += -shift if dpi_bearish else shift

    # Congressional Conviction Score Adjustments
    score = shield_data.get("score", 50)
    if score!= 50:
        boost = (score - 50) * 0.002
        modifier += boost

    return modifier

def compute_dea_size_multiplier(ticker, dea_cache, cohort_scores):
    """Calculates the bounded, relative percentile-based sizing tilt."""
    if ticker not in dea_cache or not cohort_scores or len(cohort_scores) < 4:
        return 1.0

    score = float(dea_cache[ticker].get("dea_score", 0.0)) * 100.0
    lo, hi = cohort_scores, cohort_scores[-1]
    if (hi - lo) < 1.0:
        return 1.0

    n = len(cohort_scores)
    p = sum(1 for s in cohort_scores if s <= score) / n

    if p >= 0.5:
        mult = 1.0 + ((p - 0.5) / 0.5) * 0.25
    else:
        mult = 0.5 + (p / 0.5) * 0.5
        
    return round(max(0.5, min(1.25, mult)), 3)

def push_to_dashboard(summary_stats):
    """Writes a clean compiled summary directly to the Replay_Dashboard tab."""
    print("Writing simulation summary to Google Sheets (Replay_Dashboard tab)...")
    
    # Prepare payload grid
    payload = list()
    payload.append()
    payload.append(["-------------------------", "", "", ""])
    payload.append(["Initial Paper Cash", f"${summary_stats['initial_cash']:,.2f}", "End Date", summary_stats["end_date"]])
    payload.append(["Final Paper Cash", f"${summary_stats['final_cash']:,.2f}", "Trailing Multiplier Used", f"{summary_stats['atr_multiplier']}x ATR"])
    payload.append(["Final Portfolio Value", f"${summary_stats['final_equity']:,.2f}", "Loser Leash Engaged", str(summary_stats["loser_leash"])])
    payload.append(:.2f}%", "DPI Bearish Flag", str(summary_stats["dpi_bearish"])])
    payload.append(:.2f}%", "", ""])
    payload.append(}", "", ""])
    payload.append(:.2f}%", "", ""])
    payload.append(:.2f}", "", ""])
    
    safe_payload = json.dumps(payload)
    # Clear old dashboard values first
    run_gog()
    # Write new summary
    run_gog()
    print("  [✓] Replay_Dashboard successfully updated.")

def main():
    parser = argparse.ArgumentParser(description="Kurt Standalone Online Replay Service")
    parser.add_argument("--tickers", type=str, required=True, help="Comma-separated tickers (e.g. REGN,DIS)")
    parser.add_argument("--start_date", type=str, required=True, help="Start YYYY-MM-DD")
    parser.add_argument("--days", type=int, default=60, help="Simulation duration in calendar days")
    parser.add_argument("--initial_cash", type=float, default=20000.0, help="Paper cash balance")
    parser.add_argument("--atr_multiplier", type=float, default=3.0, help="Exit trailing ATR multiplier")
    parser.add_argument("--loser_leash", type=str, default="True", help="Engage tight Loser Leash on underwater trades (True/False)")
    parser.add_argument("--dpi_bearish", type=str, default="True", help="High DPI is bearish headwind (True/False)")
    parser.add_argument("--timeframe", type=str, default="D", help="Candlestick timeframe (D, 60, etc.)")
    
    args = parser.parse_args()
    
    # Process string booleans
    loser_leash = args.loser_leash.lower() == "true"
    dpi_bearish = args.dpi_bearish.lower() == "true"
    
    RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY")
    if not RAPIDAPI_KEY:
        print("🚨 Error: RAPIDAPI_KEY environment variable not set.")
        return 1

    os.makedirs(SANDBOX_DIR, exist_ok=True)
    
    # Load caches and databases
    exchange_cache = load_json(CACHE_FILE)
    shield_cache = load_json(SHIELD_FILE)
    optimized_entries = load_json(OPTIMIZED_FILE)
    dea_cache = load_json(DEA_SCORES_FILE)
    
    tickers_list = [t.strip().upper() for t in args.tickers.split(",")]
    
    # Calculate simulation date boundaries
    start_dt = datetime.strptime(args.start_date, "%Y-%m-%d")
    end_dt = start_dt + timedelta(days=args.days)
    print(f"Initializing Replay Sandbox for {tickers_list}...")
    print(f"Simulation Range: {start_dt.strftime('%Y-%m-%d')} to {end_dt.strftime('%Y-%m-%d')} ({args.days} Days)")
    
    # Ingest historical candles per symbol
    market_data = dict()
    for ticker in tickers_list:
        prefix = exchange_cache.get(ticker, "NASDAQ:")
        symbol = f"{prefix}{ticker}"
        print(f"  Fetching historical candles for {symbol}...")
        
        # Pull enough candles to cover lookback ATR buffer and simulation window
        range_count = args.days + 30  
        candles = fetch_historical_prices(symbol, args.timeframe, range_count, RAPIDAPI_KEY)
        
        if not candles:
            print(f"  [!] Failed to fetch price history for {ticker}. Aborting.")
            return 1
        
        # Filter candles within simulation date range, keeping ATR warmup cushions
        warmup_idx = -1
        sim_candles = list()
        for idx, candle in enumerate(candles):
            # Parse TradingView time (handles string dates or raw timestamps)
            c_time_str = candle.get("time") or candle.get("date")
            try:
                if isinstance(c_time_str, (int, float)):
                    c_dt = datetime.fromtimestamp(c_time_str)
                else:
                    date_part = str(c_time_str).replace("T", " ").split(" ")
                    c_dt = datetime.strptime(date_part, "%Y-%m-%d")
            except Exception as e:
                continue
                
            if c_dt >= start_dt and warmup_idx == -1:
                warmup_idx = idx
            if start_dt <= c_dt <= end_dt:
                sim_candles.append((idx, candle, c_dt))
                
        if warmup_idx < 14:
            print(f"  [!] Insufficient historical warmup bars before {args.start_date} for ATR calculations.")
            return 1
            
        market_data[ticker] = {
            "all_candles": candles,
            "sim_candles": sim_candles
        }
        time.sleep(0.5)

    # Initialize Simulated Portfolio State
    virtual_cash = args.initial_cash
    virtual_positions = dict()  # {ticker: {"shares": n, "avg_cost": x, "highest_seen": y}}
    transactions_log = list()
    peak_portfolio_value = args.initial_cash
    min_portfolio_value = args.initial_cash
    daily_values = list()
    
    # Extract relative DEA cohort scores for watchlist weighting
    dea_cohort = sorted(
        float(dea_cache[t].get("dea_score", 0.0)) * 100.0
        for t in tickers_list if t in dea_cache
    )
    
    # Find all unique dates across all tickers' sim_candles to form a unified chronological timeline
    all_dates = set()
    for t in tickers_list:
        for _, _, c_dt in market_data[t]["sim_candles"]:
            all_dates.add(c_dt)
    sorted_dates = sorted(list(all_dates))
    
    print("\n=== STARTING SIMULATION TICK LOOP ===")
    
    # Chronological step-by-step replay loop
    for current_dt in sorted_dates:
        for ticker in tickers_list:
            ticker_data = market_data[ticker]
            # Find the candle for this ticker on current_dt
            candle_entry = next((item for item in ticker_data["sim_candles"] if item[1] == current_dt), None)
            if not candle_entry:
                continue
                
            global_idx, candle, _ = candle_entry
            
            # Candle OHLCV prices
            open_p = float(candle["open"])
            high_p = float(candle["max"])
            low_p = float(candle["min"])
            close_p = float(candle["close"])
            
            # Dynamic calculations for today
            atr = calculate_atr(ticker_data["all_candles"], global_idx)
            opt = optimized_entries.get(ticker, {})
            beta = float(opt.get("beta", 1.05))
            
            # ----------------------------------------------------
            # 1. EVALUATE ACTIVE LONG POSITIONS (Intraday Stops/Ceilings)
            # ----------------------------------------------------
            if ticker in virtual_positions:
                pos = virtual_positions[ticker]
                entry_price = pos["avg_cost"]
                shares = pos["shares"]
                
                # Check for new high-water mark
                pos["highest_seen"] = max(pos["highest_seen"], high_p)
                highest = pos["highest_seen"]
                
                # Risk Rule A: The Loser Leash (Underwater protection)
                if loser_leash and close_p < entry_price:
                    atr_pct = atr / entry_price
                    leash_pct = max(LOSER_LEASH_MIN_PCT, min(LOSER_LEASH_MAX_PCT, LOSER_LEASH_ATR_FACTOR * atr_pct))
                    current_floor = round(entry_price * (1.0 - leash_pct), 2)
                    drop_amount = round(entry_price - current_floor, 2)
                else:
                    # Risk Rule B: Standard Trailing Stop (High vs. Low Beta)
                    if beta >= BETA_THRESHOLD:
                        multiplier = args.atr_multiplier
                    else:
                        multiplier = LOW_BETA_MULTIPLIER
                    drop_amount = round(atr * multiplier, 2)
                    current_floor = round(highest - drop_amount, 2)
                
                # Check Take-Profit (SELL Target) Ceiling
                modifier = get_quiver_adjustments(ticker, shield_cache, dpi_bearish)
                base_ceiling = float(opt.get("total_return_pct", 0.0) / 100.0) * entry_price + entry_price
                if base_ceiling <= entry_price:
                    base_ceiling = entry_price + (3.0 * atr)
                target_ceiling = round(base_ceiling * modifier, 2)
                
                # Enforce dynamic sanity check (at least 1 ATR above entry)
                if target_ceiling < (entry_price + atr):
                    target_ceiling = round(entry_price + atr, 2)
                
                # --- PROCESS INTRADAY TRIGGERS ---
                # Trigger TP: High wick touches or exceeds take-profit ceiling
                if high_p >= target_ceiling:
                    exit_price = target_ceiling
                    gross_value = shares * exit_price
                    fee_deduction = gross_value * COST_PER_SIDE
                    net_value = gross_value - fee_deduction
                    
                    virtual_cash += net_value
                    profit = net_value - (shares * entry_price + (shares * entry_price * COST_PER_SIDE))
                    
                    print(f"  🎯 TAKE_PROFIT TRIGGERED: Sold {shares} {ticker} at ${exit_price:.2f} (Profit: +${profit:,.2f})")
                    transactions_log.append({
                        "date": current_dt.strftime("%Y-%m-%d"),
                        "action": "SELL",
                        "ticker": ticker,
                        "shares": shares,
                        "price": exit_price,
                        "value": net_value,
                        "notes": f"Simulated Take-Profit Ceiling Breach | Fee Deducted: ${fee_deduction:.2f}"
                    })
                    del virtual_positions[ticker]
                    
                # Trigger SL: Low wick breaches the dynamic trailing floor
                elif low_p < current_floor:
                    exit_price = open_p if open_p < current_floor else current_floor
                    gross_value = shares * exit_price
                    fee_deduction = gross_value * COST_PER_SIDE
                    net_value = gross_value - fee_deduction
                    
                    virtual_cash += net_value
                    profit = net_value - (shares * entry_price + (shares * entry_price * COST_PER_SIDE))
                    
                    print(f"  🛡️ STOP_LOSS TRIGGERED: Sold {shares} {ticker} at ${exit_price:.2f} (Loss: ${profit:,.2f})")
                    transactions_log.append({
                        "date": current_dt.strftime("%Y-%m-%d"),
                        "action": "SELL",
                        "ticker": ticker,
                        "shares": shares,
                        "price": exit_price,
                        "value": net_value,
                        "notes": f"Simulated Stop-Loss Breach | Fee Deducted: ${fee_deduction:.2f}"
                    })
                    del virtual_positions[ticker]
                    
            # ----------------------------------------------------
            # 2. EVALUATE PROSPECTIVE BUY OPPORTUNITIES (Sniper entries)
            # ----------------------------------------------------
            else:
                # Calculate entry limit trap for today
                modifier = get_quiver_adjustments(ticker, shield_cache, dpi_bearish)
                base_floor = float(opt.get("total_return_pct", 0.0) / 100.0) * close_p + close_p
                if base_floor <= 0 or base_floor >= close_p:
                    base_floor = close_p - (2.0 * atr)
                    
                target_entry = round(base_floor * modifier, 2)
                
                # Sanity check: entry price must be at least 1 ATR discount below close
                if target_entry > (close_p - atr):
                    target_entry = round(close_p - atr, 2)
                    if target_entry <= 0:
                        target_entry = round(close_p * 0.50, 2)
                        
                # Check buy trigger: Low wick drops to or below the Sniper target price
                if low_p <= target_entry and target_entry > 0:
                    entry_price = open_p if open_p < target_entry else target_entry
                    
                    # Portfolio Sizing Calculations
                    # A. Relative DEA Percentile Sizing
                    dea_multiplier = compute_dea_size_multiplier(ticker, dea_cache, dea_cohort)
                    
                    # B. Macro Sizing Multiplier (Simulating Chop State 2 constraints for safety)
                    regime_multiplier = 0.50 if beta >= BETA_THRESHOLD else 1.00
                    
                    # C. Capital Outlay Cap (Max 10% of total portfolio equity)
                    portfolio_equity = virtual_cash
                    for tick, active_pos in virtual_positions.items():
                        t_candles = market_data[tick]["sim_candles"]
                        # find the close of tick on current_dt
                        c_ent = next((item for item in t_candles if item[1] == current_dt), None)
                        current_close = float(c_ent[2]["close"]) if c_ent else float(market_data[tick]["all_candles"][-1]["close"])
                        portfolio_equity += active_pos["shares"] * current_close
                        
                    max_capital_allowed = portfolio_equity * 0.10
                    
                    # Fetch Catalyst multiplier from quiver_shield
                    shield_data = shield_cache.get(ticker, {})
                    catalyst_score = shield_data.get("catalyst_score", 0)
                    if catalyst_score >= 50:
                        catalyst_multiplier = 1.50
                    elif catalyst_score >= 30:
                        catalyst_multiplier = 1.25
                    else:
                        catalyst_multiplier = 1.00
                    
                    risk_dollar_amount = portfolio_equity * (0.01 * catalyst_multiplier * regime_multiplier * dea_multiplier)
                    m_val = opt.get("exit_multiplier_used", 3.0)
                    
                    target_shares = 0
                    if atr > 0 and m_val > 0 and risk_dollar_amount > 0:
                        target_shares = math.floor(risk_dollar_amount / (atr * m_val))
                        
                    # Outlay Cap constraint
                    position_cost = target_shares * entry_price
                    if position_cost > max_capital_allowed:
                        target_shares = math.floor(max_capital_allowed / entry_price)
                        position_cost = target_shares * entry_price
                        
                    # Safe 1-share minimum override
                    if target_shares == 0 and portfolio_equity >= entry_price:
                        target_shares = 1
                        position_cost = entry_price
                        
                    total_outlay = position_cost + (position_cost * COST_PER_SIDE)
                    
                    if target_shares > 0 and virtual_cash >= total_outlay:
                        virtual_cash -= total_outlay
                        virtual_positions[ticker] = {
                            "shares": target_shares,
                            "avg_cost": entry_price,
                            "highest_seen": high_p
                        }
                        print(f"  🎯 BUY LIMIT REACHED: Bought {target_shares} {ticker} at ${entry_price:.2f} (Total Outlay: ${total_outlay:,.2f})")
                        transactions_log.append({
                            "date": current_dt.strftime("%Y-%m-%d"),
                            "action": "BUY",
                            "ticker": ticker,
                            "shares": target_shares,
                            "price": entry_price,
                            "value": total_outlay,
                            "notes": f"Simulated Sniper Entry Fill | Fee Deducted: ${position_cost * COST_PER_SIDE:.2f}"
                        })

        # Calculate daily portfolio liquidation equity curve
        current_equity = virtual_cash
        for tick, pos in virtual_positions.items():
            # Find the close price of this tick on current_dt
            tick_data = market_data[tick]
            c_entry = next((item for item in tick_data["sim_candles"] if item[1] == current_dt), None)
            if c_entry:
                current_close = float(c_entry[2]["close"])
            else:
                current_close = float(tick_data["all_candles"][-1]["close"])
            current_equity += pos["shares"] * current_close
            
        peak_portfolio_value = max(peak_portfolio_value, current_equity)
        min_portfolio_value = min(min_portfolio_value, current_equity)
        daily_values.append(current_equity)

    # ----------------------------------------------------
    # 3. PERFORMANCE COMPILATION & STATISTICS REPORT
    # ----------------------------------------------------
    print("\n=== REPLAY STATISTICS AUDIT ===")
    final_equity = virtual_cash
    for tick, pos in virtual_positions.items():
        t_candles = market_data[tick]["sim_candles"]
        current_close = float(t_candles[-1][2]["close"])
        final_equity += pos["shares"] * current_close
        
    total_return_pct = ((final_equity - args.initial_cash) / args.initial_cash) * 100.0
    
    trades_count = len(transactions_log)
    completed_trades = [t for t in transactions_log if t["action"] == "SELL"]
    wins = 0
    for t in completed_trades:
        buy_tx = next((b for b in transactions_log if b["ticker"] == t["ticker"] and b["action"] == "BUY"), None)
        if buy_tx and t["value"] > buy_tx["value"]:
            wins += 1
            
    win_rate_pct = (wins / len(completed_trades)) * 100.0 if completed_trades else 0.0
    
    # Calculate Max Peak-to-Trough Drawdown
    max_dd_pct = 0.0
    for idx, val in enumerate(daily_values):
        peak = max(daily_values[:idx+1])
        dd = ((peak - val) / peak) * 100.0
        max_dd_pct = max(max_dd_pct, dd)
        
    # Calculate annualized Sharpe Ratio (assuming risk-free rate of 4.5% or 0.012% daily)
    returns_series = pd.Series(daily_values).pct_change().dropna()
    if not returns_series.empty and returns_series.std() > 0:
        excess_returns = returns_series - (0.045 / 252)
        sharpe = (excess_returns.mean() / returns_series.std()) * math.sqrt(252)
    else:
        sharpe = 0.0
        
    print(f"  Initial Equity: ${args.initial_cash:,.2f}")
    print(f"  Final Liquid Cash: ${virtual_cash:,.2f}")
    print(f"  Final Portfolio Value: ${final_equity:,.2f}")
    print(f"  Total Realized Return: {total_return_pct:.2f}%")
    print(f"  Trades Executed: {trades_count} ({len(completed_trades)} Completed)")
    print(f"  Simulated Win Rate: {win_rate_pct:.2f}%")
    print(f"  Max Simulated Drawdown: {max_dd_pct:.2f}%")
    print(f"  Simulated Sharpe Ratio: {sharpe:.2f}")
    
    # Compile summary object
    summary_stats = {
        "start_date": args.start_date,
        "end_date": end_dt.strftime("%Y-%m-%d"),
        "initial_cash": args.initial_cash,
        "final_cash": virtual_cash,
        "final_equity": final_equity,
        "atr_multiplier": args.atr_multiplier,
        "loser_leash": loser_leash,
        "dpi_bearish": dpi_bearish,
        "total_return_pct": total_return_pct,
        "win_rate_pct": win_rate_pct,
        "trades_count": trades_count,
        "max_dd_pct": max_dd_pct,
        "sharpe": sharpe
    }
    
    # Persist simulation state locally (atomic saves)
    save_json_atomic(virtual_positions, REPLAY_POSITIONS_FILE)
    save_json_atomic(summary_stats, os.path.join(SANDBOX_DIR, "replay_history.json"))
    
    # Write transactions log to local CSV
    df_tx = pd.DataFrame(transactions_log)
    if not df_tx.empty:
        df_tx.to_csv(REPLAY_TRANSACTIONS_FILE, index=False)
        print(f"  [✓] Transactions logged to {REPLAY_TRANSACTIONS_FILE}")
        
    # Push the compiled post-run summary to Google Sheets Replay_Dashboard tab
    push_to_dashboard(summary_stats)
    return 0

if __name__ == "__main__":
    sys.exit(main() or 0)
