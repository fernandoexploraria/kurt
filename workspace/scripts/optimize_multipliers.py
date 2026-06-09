import yfinance as yf
import pandas as pd
import json
import os
import subprocess
from datetime import datetime

OUTPUT_FILE = "/root/.openclaw/workspace/memory/optimized_multipliers.json"
LIVE_SHEET_ID = "1kjzfc6uEzBFtmNjlU1x3TVbHuWPgY7jnNce8mNTe66I"
ACCOUNT = "fernando@exploraria.ai"

def run_gog(command):
    full_cmd = f"GOG_ACCOUNT={ACCOUNT} gog sheets {command} --json"
    result = subprocess.run(full_cmd, shell=True, capture_output=True, text=True)
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
    pos_data = run_gog(f'get {LIVE_SHEET_ID} "Positions!A4:A100"')
    if pos_data and "values" in pos_data:
        for row in pos_data["values"]:
            if row and row[0].strip() and row[0].strip() != "CASH":
                tickers.add(row[0].strip())
                
    # Get Watchlist
    watch_data = run_gog(f'get {LIVE_SHEET_ID} "Watchlist!A2:A100"')
    if watch_data and "values" in watch_data:
        for row in watch_data["values"]:
            if row and row[0].strip():
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

def backtest_strategy(df, m, strategy_name):
    in_position = False
    wait_for_reset = False 
    entry_price = 0.0
    highest_seen = 0.0
    total_compound_return = 1.0
    trades = 0
    wins = 0

    for index, row in df.iterrows():
        close = float(row['Close'])
        open_price = float(row['Open'])
        high = float(row['High'])
        low = float(row['Low'])
        atr = float(row['ATR'])
        ema_50 = float(row['EMA_50'])
        ema_200 = float(row['EMA_200'])
        rsi = float(row['RSI'])

        if pd.isna(atr) or pd.isna(ema_200) or pd.isna(rsi):
            continue

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

            # If the trigger condition is no longer met, the trend has reset.
            if not triggered:
                wait_for_reset = False

            # Only enter if the trigger is met AND we are not in a cooldown.
            if triggered and not wait_for_reset:
                in_position = True
                entry_price = close
                highest_seen = high
                trades += 1
        else:
            highest_seen = max(highest_seen, high)
            trailing_floor = highest_seen - (atr * m)

            if low < trailing_floor:
                in_position = False
                wait_for_reset = True 
                exit_price = open_price if open_price < trailing_floor else trailing_floor
                trade_return = (exit_price - entry_price) / entry_price
                total_compound_return *= (1 + trade_return)
                if trade_return > 0: wins += 1

    if in_position:
        end_price = float(df['Close'].iloc[-1])
        trade_return = (end_price - entry_price) / entry_price
        total_compound_return *= (1 + trade_return)
        if trade_return > 0: wins += 1

    final_return = (total_compound_return - 1) * 100
    win_rate = (wins / trades * 100) if trades > 0 else 0.0

    return final_return, win_rate, trades

def main():
    print(f"[{datetime.now().isoformat()}] Starting Weekly Quant Optimizer...")
    
    universe = get_master_universe()
    if not universe:
        print("Failed to pull universe. Exiting.")
        return
        
    print(f"Found {len(universe)} unique tickers to optimize.\n")
        
    multipliers_to_test = [1.5, 2.0, 2.5, 3.0, 3.5, 4.0]
    optimized_results = {}

    for ticker in universe:
        print(f"Optimizing {ticker}...", end=" ", flush=True)
        try:
            yf_ticker = ticker.replace(".", "-")
            df = yf.download(yf_ticker, period="3y", progress=False)
            if df.empty:
                print("Failed (No Data)")
                continue
            
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            df['Prev_Close'] = df['Close'].shift(1)
            df['TR1'] = df['High'] - df['Low']
            df['TR2'] = abs(df['High'] - df['Prev_Close'])
            df['TR3'] = abs(df['Low'] - df['Prev_Close'])
            df['TR'] = df[['TR1', 'TR2', 'TR3']].max(axis=1)
            df['ATR'] = df['TR'].rolling(window=14).mean()
            df['EMA_50'] = df['Close'].ewm(span=50, adjust=False).mean()
            df['EMA_200'] = df['Close'].ewm(span=200, adjust=False).mean()
            df['RSI'] = calculate_rsi(df)
            
            best_m = 3.0
            best_strat = "None"
            best_return = -999.0
            
            strategies = ["RSI_30", "RSI_40", "EMA_50_Bounce", "EMA_200_Bounce", "Holy_Grail"]

            # Walk-Forward Validation (66% Train / 34% Validate)
            split_idx = int(len(df) * 0.66)
            df_train = df.iloc[:split_idx]
            df_val = df.iloc[split_idx:]

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
                    robust_candidate = (strat, m)
                    break

            if robust_candidate:
                best_strat, best_m = robust_candidate
            else:
                # If nothing survived validation, fall back to purely defensive default
                best_strat, best_m = "None", 3.0

            if best_strat != "None":
                best_return, _, _ = backtest_strategy(df, best_m, best_strat)
            
            optimized_results[ticker] = best_m
            print(f"Best: {best_m}x with {best_strat} ({best_return:.2f}%)")
            
        except Exception as e:
            print(f"Failed ({e})")
            optimized_results[ticker] = 3.0 

    with open(OUTPUT_FILE, 'w') as f:
        json.dump(optimized_results, f, indent=2)
        
    print(f"\nOptimization complete. Saved {len(optimized_results)} profiles to {OUTPUT_FILE}.")

if __name__ == "__main__":
    main()
