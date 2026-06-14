import json
import subprocess
import os
from datetime import datetime
import time
import requests
import math
import yfinance as yf
import pandas as pd
import numpy as np

TEST_SHEET_ID = "1kjzfc6uEzBFtmNjlU1x3TVbHuWPgY7jnNce8mNTe66I"
ACCOUNT = "fernando@exploraria.ai"
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY")
if not RAPIDAPI_KEY:
    import sys
    sys.exit("Error: RAPIDAPI_KEY environment variable not set.")
RAPIDAPI_HOST = "tradingview-data1.p.rapidapi.com"
CACHE_FILE = "/root/.openclaw/workspace/memory/exchange_cache.json"
SHIELD_FILE = "/root/.openclaw/workspace/memory/quiver_shield.json"
ORDERS_FILE = "/root/.openclaw/workspace/memory/pending_orders.json"

def load_shield():
    if os.path.exists(SHIELD_FILE):
        try:
            with open(SHIELD_FILE, 'r') as f:
                return json.load(f)
        except: pass
    return {}

def load_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'r') as f:
                return json.load(f)
        except: pass
    return {}

def save_cache(cache):
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    with open(CACHE_FILE, 'w') as f:
        json.dump(cache, f, indent=2)

def run_subprocess(cmd_list):
    """
    UPGRADE P0-4: Executes an external command securely using list-based
    arguments with shell=False to prevent shell injection [P0-4].
    """
    result = subprocess.run(cmd_list, shell=False, capture_output=True, text=True)
    if result.returncode != 0: return None
    return result.stdout.strip()

def run_gog(args_list):
    """
    UPGRADE P0-4: Executes 'gog sheets' securely with an isolated environment map.
    """
    env = os.environ.copy()
    env["GOG_ACCOUNT"] = ACCOUNT
    cmd_list = ["gog", "sheets"] + args_list
    result = subprocess.run(cmd_list, env=env, shell=False, capture_output=True, text=True)
    if result.returncode != 0: return None
    try:
        return json.loads(result.stdout.strip())
    except:
        return None

def get_total_equity():
    out = run_gog(["get", TEST_SHEET_ID, "Positions!F3", "--json"])
    if not out: return 0.0
    data = out
    try:
        val = data.get('values', [['0']])[0][0]
        return float(str(val).replace(',', '').replace('$', ''))
    except:
        return 0.0

def get_active_positions():
    out = run_gog(["get", TEST_SHEET_ID, "Positions!A4:A50", "--json"])
    if not out: return []
    data = out
    tickers = []
    for row in data.get('values', []):
        if len(row) > 0 and row[0].strip() and row[0].strip() != "CASH":
            tickers.append(row[0].strip())
    return tickers

def get_macro_regime(active_positions):
    spy_close = 0.0
    spy_sma200 = 0.0
    vix_close = 0.0
    avg_portfolio_corr = 0.0
    
    try:
        df_macro = yf.download(["SPY", "^VIX"], period="1y", progress=False)['Close']
        if not df_macro.empty:
            if "SPY" in df_macro.columns:
                spy_series = df_macro["SPY"].dropna()
                if not spy_series.empty:
                    spy_close = spy_series.iloc[-1]
                    if len(spy_series) >= 200:
                        spy_sma200 = spy_series.rolling(window=200).mean().iloc[-1]
            if "^VIX" in df_macro.columns:
                vix_series = df_macro["^VIX"].dropna()
                if not vix_series.empty:
                    vix_close = vix_series.iloc[-1]
        
        if active_positions and len(active_positions) > 1:
            yf_active = [p.replace(".", "-") for p in active_positions]
            df_port = yf.download(yf_active, period="90d", progress=False)['Close']
            if not df_port.empty:
                corr_matrix = df_port.corr()
                mask = np.triu(np.ones(corr_matrix.shape, dtype=bool), k=1)
                avg_val = corr_matrix.where(mask).mean().mean()
                if pd.notna(avg_val):
                    avg_portfolio_corr = float(avg_val)
    except Exception as e:
        print(f"  [!] Error fetching macro regime data: {e}")
        
    regime_state = 1
    if spy_sma200 > 0: # Ensure valid SPY data before applying logic
        if (spy_close < spy_sma200) or (vix_close > 25) or (avg_portfolio_corr >= 0.50):
            regime_state = 3
        elif (spy_close >= spy_sma200) and ((vix_close >= 20) or (avg_portfolio_corr >= 0.35)):
            regime_state = 2
            
    return regime_state, spy_close, spy_sma200, vix_close, avg_portfolio_corr

def get_beta(ticker, cache):
    """
    Retrieves the 1-year historical beta using the fast, cached TradingView RapidAPI.
    Prevents the latency, rate-limiting, and silent default errors of yfinance.
    """
    prefixes = ["NASDAQ:", "NYSE:", "AMEX:", "BATS:", "CRYPTO:"]
    if ticker in cache:
        prefixes = [cache[ticker]] + [p for p in prefixes if p != cache[ticker]]

    headers = {
        "x-rapidapi-key": RAPIDAPI_KEY,
        "x-rapidapi-host": RAPIDAPI_HOST
    }

    for prefix in prefixes:
        url = f"https://{RAPIDAPI_HOST}/api/quote/{prefix}{ticker}?fields=beta_1_year"
        try:
            res = requests.get(url, headers=headers, timeout=10).json()
            if res.get("success"):
                # Dynamically cache the successful prefix
                if ticker not in cache or cache[ticker] != prefix:
                    cache[ticker] = prefix
                    save_cache(cache)
                try:
                    return float(res["data"]["data"]["beta_1_year"])
                except:
                    return 1.0
        except:
            pass
        time.sleep(0.1) # Minimal throttling for RapidAPI
    return 1.0

def get_watchlist():
    out = run_gog(["get", TEST_SHEET_ID, "Watchlist!A2:H50", "--json"])
    if not out: return []
    data = out
    tickers = []
    for i, row in enumerate(data.get('values', [])):
        if len(row) > 0 and row[0].strip() and row[0].strip().upper() != "TICKER":
            price = 0.0
            atr = 0.0
            if len(row) > 2 and row[2].strip():
                try: price = float(str(row[2]).replace(',', '').replace('$', ''))
                except: pass
            if len(row) > 7 and row[7].strip():
                try: atr = float(str(row[7]).replace(',', '').replace('$', ''))
                except: pass
            
            tickers.append({"row": i + 2, "ticker": row[0].strip(), "price": price, "atr": atr})
    return tickers

def get_ta_floor(ticker, cache, strategy=None):
    prefixes = ["NASDAQ:", "NYSE:", "AMEX:", "BATS:", "CRYPTO:"]
    # Option 3 Magic: Move known prefix to the front of the line
    if ticker in cache:
        prefixes = [cache[ticker]] + [p for p in prefixes if p != cache[ticker]]

    headers = {"x-rapidapi-key": RAPIDAPI_KEY, "x-rapidapi-host": RAPIDAPI_HOST}
    for prefix in prefixes:
        url = f"https://{RAPIDAPI_HOST}/api/ta/{prefix}{ticker}/indicators"
        try:
            res = requests.get(url, headers=headers, timeout=10).json()
            if res.get("success"):
                # Save the successful prefix
                if ticker not in cache or cache[ticker] != prefix:
                    cache[ticker] = prefix
                    save_cache(cache)
                ind = res["data"]
                
                # --- STRATEGY TRANSLATION ENGINE ---
                if strategy == "EMA_50_Bounce" and ind.get("EMA50"):
                    return ind["EMA50"]
                elif strategy in ["EMA_200_Bounce", "Holy_Grail"] and ind.get("EMA200"):
                    return ind["EMA200"]
                elif strategy == "RSI_40" and ind.get("Pivot.M.Classic.S1"):
                    return ind["Pivot.M.Classic.S1"]
                elif strategy == "RSI_30" and ind.get("Pivot.M.Classic.S2"):
                    return ind["Pivot.M.Classic.S2"]
                
                # Fallback if specific strategy indicator fails or strategy is None
                if ind.get("EMA200"): return ind["EMA200"]
                elif ind.get("Pivot.M.Classic.S1"): return ind["Pivot.M.Classic.S1"]
                elif ind.get("Pivot.M.Fibonacci.S1"): return ind["Pivot.M.Fibonacci.S1"]
        except: pass
        time.sleep(0.3)
    return None

def get_52w_low(ticker, cache):
    prefixes = ["NASDAQ:", "NYSE:", "AMEX:", "BATS:", "CRYPTO:"]
    if ticker in cache:
        prefixes = [cache[ticker]] + [p for p in prefixes if p != cache[ticker]]

    headers = {"x-rapidapi-key": RAPIDAPI_KEY, "x-rapidapi-host": RAPIDAPI_HOST}
    for prefix in prefixes:
        url = f"https://{RAPIDAPI_HOST}/api/quote/{prefix}{ticker}"
        try:
            res = requests.get(url, headers=headers, timeout=10).json()
            if res.get("success"):
                if ticker not in cache or cache[ticker] != prefix:
                    cache[ticker] = prefix
                    save_cache(cache)
                return res["data"]["data"].get("price_52_week_low")
        except: pass
        time.sleep(0.3)
    return None


def get_atr(ticker, cache):
    prefixes = ["NASDAQ:", "NYSE:", "AMEX:", "BATS:", "CRYPTO:"]
    if ticker in cache:
        prefixes = [cache[ticker]] + [p for p in prefixes if p != cache[ticker]]

    headers = {"x-rapidapi-key": RAPIDAPI_KEY, "x-rapidapi-host": RAPIDAPI_HOST}
    hist = None
    
    for prefix in prefixes:
        url = f"https://{RAPIDAPI_HOST}/api/price/{prefix}{ticker}?range=16&timeframe=D"
        try:
            res = requests.get(url, headers=headers, timeout=10).json()
            if res.get("success"):
                if ticker not in cache or cache[ticker] != prefix:
                    cache[ticker] = prefix
                    save_cache(cache)
                hist = res["data"]["history"]
                break
        except: pass
        time.sleep(0.2)
        
    if hist and len(hist) > 1:
        trs = []
        for j in range(1, len(hist)):
            h = hist[j]["max"]
            l = hist[j]["min"]
            pc = hist[j-1]["close"]
            tr = max(h - l, abs(h - pc), abs(l - pc))
            trs.append(tr)
        
        atr = sum(trs[-14:]) / min(14, len(trs))
        return round(atr, 2)
        
    return None

def get_quiver_adjustments(ticker, shield_cache):
    modifier = 1.0 
    shield_data = shield_cache.get(ticker, {})
    
    # 1. Dark Pool Index (DPI) Adjustments
    latest_dpi = shield_data.get("dpi", 0.5)
    if latest_dpi > 0.50:
        reduction = (latest_dpi - 0.50) * 0.2
        modifier -= min(reduction, 0.05)

    # 2. Congressional Conviction Score Adjustments (Volume-Weighted)
    score = shield_data.get("score", 50)
    if score != 50:
        # Scale: (Score - 50) * 0.002. 
        # Score 100 -> +0.10 boost. Score 0 -> -0.10 discount.
        boost = (score - 50) * 0.002
        modifier += boost
        
    return modifier

def get_correlation_multiplier(candidate, active_positions):
    """
    UPGRADE B: Calculates a Dual-Engine correlation multiplier.
    Combines Continuous Pairwise Max and Portfolio-Level Average Correlation,
    enforcing the strictest decay.
    """
    if not active_positions:
        return 1.0, None, "No active positions"

    yf_candidate = candidate.replace(".", "-")
    yf_active = [p.replace(".", "-") for p in active_positions]
    check_list = [p for p in yf_active if p != yf_candidate]

    if not check_list:
        return 1.0, None, "No overlapping active positions"

    tickers_to_dl = check_list + [yf_candidate]

    try:
        df = yf.download(tickers_to_dl, period="90d", progress=False)['Close']
        if df.empty:
            return 1.0, None, "No data fetched"

        corr_matrix = df.corr()
        if yf_candidate not in corr_matrix.columns:
            return 1.0, None, "Candidate not in matrix"

        # --- ENGINE 1: CONTINUOUS PAIRWISE MAX ---
        max_corr = 0.0
        worst_tick = None
        for active_tick in check_list:
            if active_tick in corr_matrix.columns:
                corr_val = corr_matrix.loc[yf_candidate, active_tick]
                if pd.notna(corr_val) and corr_val > max_corr:
                    max_corr = corr_val
                    worst_tick = active_tick.replace("-", ".")

        m_pairwise = 1.0
        if worst_tick is not None:
            m_pairwise = max(0.5, 1.0 - max(0.0, max_corr - 0.50))

        # --- ENGINE 2: PORTFOLIO-LEVEL AVERAGE CORRELATION ---
        # Isolate the candidate's correlation vector across check_list
        valid_ticks = [t for t in check_list if t in corr_matrix.columns]
        if valid_ticks:
            portfolio_vector = corr_matrix.loc[yf_candidate, valid_ticks]
            avg_corr = portfolio_vector.mean()
            # Average correlation decay above 0.30 threshold
            m_portfolio_avg = max(0.5, 1.0 - max(0.0, avg_corr - 0.30))
        else:
            avg_corr = 0.0
            m_portfolio_avg = 1.0

        # --- MINIMUM-OPERATOR SELECTION ---
        # Choose the strictest risk penalty
        if m_portfolio_avg < m_pairwise:
            final_multiplier = m_portfolio_avg
            trigger_reason = f"Systemic Portfolio Avg Correlation: {avg_corr:.2f}"
        else:
            final_multiplier = m_pairwise
            trigger_reason = f"Pairwise Overlap w/ {worst_tick} ({max_corr:.2f})"

        return round(final_multiplier, 4), worst_tick, trigger_reason

    except Exception as e:
        print(f"  [!] Correlation calculation error: {e}")
        return 1.0, None, "Calculation Error"

def update_sheet(row, entry_price, confidence_str, notes_str):
    today = datetime.now().strftime("%Y-%m-%d")
    run_gog(["update", TEST_SHEET_ID, f"Watchlist!D{row}", "--values-json", f"[[{entry_price}]]", "--input", "USER_ENTERED"])
    run_gog(["update", TEST_SHEET_ID, f"Watchlist!E{row}", "--values-json", f'[[\"=(C{row}-D{row})/D{row}\"]]', "--input", "USER_ENTERED"])
    run_gog(["update", TEST_SHEET_ID, f"Watchlist!F{row}", "--values-json", f'[["{confidence_str}"]]', "--input", "USER_ENTERED"])
    run_gog(["update", TEST_SHEET_ID, f"Watchlist!G{row}", "--values-json", f'[["{notes_str}"]]', "--input", "USER_ENTERED"])
    run_gog(["update", TEST_SHEET_ID, f"Watchlist!I{row}", "--values-json", f'[["{today}"]]', "--input", "USER_ENTERED"])

def sync_pending_orders(new_targets):
    if not os.path.exists(ORDERS_FILE):
        return
    try:
        with open(ORDERS_FILE, 'r') as f:
            orders = json.load(f)
    except:
        return
        
    updated = False
    print("\n=== SYNCHRONIZING LIMIT SNIPER TRAPS ===")
    for ticker, data in orders.items():
        if data.get("status") == "waiting" and data.get("action") == "BUY":
            if ticker in new_targets:
                old_price = data.get("target_price")
                new_price = new_targets[ticker]
                if old_price != new_price:
                    print(f"  [Sync] Updating {ticker} Trap: ${old_price} -> ${new_price}")
                    data["target_price"] = new_price
                    updated = True
                    
    if updated:
        with open(ORDERS_FILE, 'w') as f:
            json.dump(orders, f, indent=2)
        print("  [✓] All pending traps synchronized successfully.")
    else:
        print("  [-] No waiting traps required updating.")

def main():
    print("Starting V7 Watchlist Calibration (Unified & Secured)...")
    cache = load_cache()
    shield_cache = load_shield()
    
    total_equity = get_total_equity()
    print(f"[i] Live Portfolio Total Equity: ${total_equity:,.2f}")
    
    active_positions = get_active_positions()
    print(f"[i] Active Positions Found: {len(active_positions)}")
    
    # --- PHASE 1: SYSTEMIC MACRO REGIME DETERMINATION ---
    regime_state, spy_close, spy_sma200, vix_close, avg_portfolio_corr = get_macro_regime(active_positions)
    print(f"[i] Macro Regime State: {regime_state} | SPY: ${spy_close:.2f} (SMA200: ${spy_sma200:.2f}) | VIX: {vix_close:.2f} | Avg Corr: {avg_portfolio_corr:.2f}")

    # Load Optimized Brain
    optimized_file = "/root/.openclaw/workspace/memory/optimized_entries.json"
    optimized_entries = {}
    if os.path.exists(optimized_file):
        try:
            with open(optimized_file, 'r') as f:
                optimized_entries = json.load(f)
        except: pass

    tickers = get_watchlist()
    results = []
    new_targets = {}

    for item in tickers:
        ticker, row, current_price, old_atr = item['ticker'], item['row'], item['price'], item['atr']
        print(f"\n--- {ticker} ---")
        
        # Determine Optimized Strategy
        opt = optimized_entries.get(ticker, {})
        strategy = opt.get("best_trigger", None)
        if strategy:
            print(f"  [+] Optimizer Brain: Engaged (Strategy: {strategy})")
        else:
            print(f"  [!] No Optimized Strategy Found. Using fallbacks.")
            
        # --- PHASE 3: FETCH AND WRITE LIVE ATR ---
        live_atr = get_atr(ticker, cache)
        if live_atr:
            atr = live_atr
            print(f"  [+] Live ATR Calculated: ${atr:.2f}")
            run_gog(["update", TEST_SHEET_ID, f"Watchlist!H{row}", "--values-json", f"[[{atr}]]", "--input", "USER_ENTERED"])
        else:
            atr = old_atr if old_atr > 0 else 1.0
            print(f"  [!] Failed to fetch Live ATR. Falling back to sheet/default value: ${atr:.2f}")
            
        base_floor = get_ta_floor(ticker, cache, strategy)
        if base_floor: 
            print(f"  [+] Found TA Structural Floor: ${base_floor:.2f}")
        else:
            low_52w = get_52w_low(ticker, cache)
            if low_52w:
                print(f"  [!] No TA Floor. Using 52W Low + 5% Buffer: ${low_52w * 1.05:.2f}")
                base_floor = low_52w * 1.05
            else:
                print(f"  [!] Absolute failure. Falling back to Current Price - 2x ATR.")
                base_floor = current_price - (2 * atr)
        
        modifier = get_quiver_adjustments(ticker, shield_cache)
        target_entry = round(base_floor * modifier, 2)
        
        if current_price > 0 and target_entry > (current_price - atr):
            sanity_target = current_price - atr
            # The ABTS negative price fix:
            if sanity_target <= 0:
                sanity_target = current_price * 0.50 # Hard limit a 50% drop if ATR math breaks
            print(f"  [!] Sanity Check: Entry too close. Forcing -1 ATR discount (${sanity_target:.2f}).")
            target_entry = round(sanity_target, 2)
            
        print(f"  [=] FINAL SNIPER ENTRY: ${target_entry}")
        
        # --- PHASE 4: DYNAMIC POSITION SIZING (RISK PARITY & CORRELATION) ---
        m = opt.get("exit_multiplier_used", 3.0)
        
        # Advisor Note: Catalyst Multiplier Integration
        shield_data = shield_cache.get(ticker, {})
        catalyst_score = shield_data.get("catalyst_score", 0)
        
        if catalyst_score >= 50:
            catalyst_multiplier = 1.50
            sizing_note = f"🚀 1.5x Sizing: Monumental Catalyst ({catalyst_score} pts)"
        elif catalyst_score >= 30:
            catalyst_multiplier = 1.25
            sizing_note = f"⚡ 1.25x Sizing: High Momentum ({catalyst_score} pts)"
        else:
            catalyst_multiplier = 1.00
            sizing_note = "Standard Sizing"
            
        # Scale the At-Risk Capital
        # --- PATH B: MULTI-STATE REGIME SIZING ---
        beta = get_beta(ticker, cache)
        regime_multiplier = 1.00
        
        if regime_state == 2:
            if beta >= 1.05:
                regime_multiplier = 0.50 # Squeeze growth plays during chop
                if sizing_note == "Standard Sizing": sizing_note = ""
                sizing_note += " | ⚠️ Chop Regime: 0.5x Growth Penalty"
        elif regime_state == 3:
            if beta >= 1.05:
                regime_multiplier = 0.00 # Zero risk allocated to tech during panic
                if sizing_note == "Standard Sizing": sizing_note = ""
                sizing_note += " | 🛑 Bear Regime: speculative trades DISABLED"
            else:
                regime_multiplier = 0.50 # Scale back defensive buys
                if sizing_note == "Standard Sizing": sizing_note = ""
                sizing_note += " | 🛡️ Bear Regime: 0.5x Defensive Scaling"
                
        scaled_risk_pct = 0.01 * catalyst_multiplier * regime_multiplier
        risk_dollar_amount = total_equity * scaled_risk_pct
        
        target_shares = 0
        if target_entry > 0 and atr > 0 and m > 0 and regime_multiplier > 0:
            target_shares = math.floor(risk_dollar_amount / (atr * m))
            
        # The 'At Least One' Override
        if target_shares == 0 and total_equity >= target_entry and target_entry > 0 and regime_multiplier > 0:
            target_shares = 1
            
        # --- UPGRADE B INTEGRATION: Dual-Engine Correlation Sizing ---
        corr_multiplier, highly_correlated_ticker, trigger_reason = get_correlation_multiplier(ticker, active_positions)
        
        if corr_multiplier < 1.0 and regime_multiplier > 0:
            target_shares = math.floor(target_shares * corr_multiplier)
            if target_shares == 0 and total_equity >= target_entry and target_entry > 0:
                target_shares = 1 
                
        # Advisor Note: The Capital Outlay Cap
        max_capital_allowed = total_equity * 0.10 # Max 10% of portfolio equity in physical cash outlay
        position_cost = target_shares * target_entry

        if position_cost > max_capital_allowed:
            target_shares = math.floor(max_capital_allowed / target_entry)
            sizing_note += " | 🛑 Capped by 10% Outlay Limit"
            position_cost = target_shares * target_entry # Recalculate

        confidence_str = f"{scaled_risk_pct*100:.1f}% Risk"
        if corr_multiplier < 1.0 and regime_multiplier > 0:
            # Scale risk visually based on the exact continuous multiplier
            active_risk_pct = (scaled_risk_pct * corr_multiplier) * 100
            confidence_str = f"{active_risk_pct:.2f}% Risk"
            notes_str = f"Buy {target_shares} Shares (~${position_cost:,.2f}) | ⚠️ {corr_multiplier:.2f}x Correlation Sizing: {trigger_reason}"
            if sizing_note != "Standard Sizing" and sizing_note != "":
                 notes_str += f" {sizing_note}"
            print(f"  [+] Dynamic Sizing: {confidence_str} ({trigger_reason}) | Buy {target_shares} shares")
        else:
            if regime_multiplier == 0:
                notes_str = f"Buy 0 Shares (~$0.00)"
                if sizing_note != "Standard Sizing" and sizing_note != "":
                     notes_str += f" {sizing_note}"
                print(f"  [+] Dynamic Sizing: {confidence_str} | {notes_str}")
            else:
                notes_str = f"Buy {target_shares} Shares (~${position_cost:,.2f})"
                if sizing_note != "Standard Sizing" and sizing_note != "":
                     notes_str += f" {sizing_note}"
                print(f"  [+] Dynamic Sizing: {confidence_str} | {notes_str} (Multiplier: {m}x)")
        
        update_sheet(row, target_entry, confidence_str, notes_str)
        
        new_targets[ticker] = target_entry
        pct_to_entry = ((current_price - target_entry) / target_entry) * 100 if target_entry > 0 else 999
        results.append({"ticker": ticker, "entry": target_entry, "pct": pct_to_entry})

    print("\n=== TOP 3 ACTIONABLE (IMMINENT ENTRIES) ===")
    results.sort(key=lambda x: x['pct'])
    for r in results[:3]: print(f"{r['ticker']} - Entry: ${r['entry']} ({r['pct']:.2f}% away)")
    
    sync_pending_orders(new_targets)

if __name__ == "__main__":
    main()
