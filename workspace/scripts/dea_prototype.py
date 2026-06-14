#!/usr/bin/env python3
import json
import subprocess
import os
import re
import numpy as np
from scipy.optimize import linprog

LIVE_SHEET_ID = "1kjzfc6uEzBFtmNjlU1x3TVbHuWPgY7jnNce8mNTe66I"
ACCOUNT = "fernando@exploraria.ai"
ENTRIES_FILE = "/root/.openclaw/workspace/memory/optimized_entries.json"

def run_gog(args_list):
    env = os.environ.copy()
    env["GOG_ACCOUNT"] = ACCOUNT
    cmd_list = ["gog", "sheets"] + args_list
    result = subprocess.run(cmd_list, env=env, shell=False, capture_output=True, text=True)
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout.strip())
    except:
        return None

def main():
    print("=== DEA Prototype for Equity Selection ===")
    
    # 1. Load data
    print("Loading optimized entries...")
    entries_data = {}
    if os.path.exists(ENTRIES_FILE):
        try:
            with open(ENTRIES_FILE, 'r') as f:
                entries_data = json.load(f)
        except Exception as e:
            print(f"Error loading entries: {e}")

    print("Fetching Watchlist from Google Sheets...")
    watchlist_data = run_gog(["get", LIVE_SHEET_ID, "Watchlist!A:J", "--json"])
    if not watchlist_data or "values" not in watchlist_data:
        print("Failed to fetch watchlist.")
        return

    rows = watchlist_data["values"]
    if len(rows) == 0:
        return

    # Extract metrics
    dmus = []
    
    for i, row in enumerate(rows):
        if i == 0: continue
        if len(row) == 0: continue
        ticker = row[0].strip()
        if not ticker or ticker == "CASH" or ticker.upper() == "TICKER": continue
        
        # Safe parse helpers
        def get_val(idx, default=0.0):
            if len(row) > idx and row[idx].strip():
                try: return float(row[idx].replace('$', '').replace(',', '').replace('%', ''))
                except: return default
            return default
            
        price = get_val(2, 0.0)
        atr = get_val(7, 0.0)
        quiver_str = row[9] if len(row) > 9 else ""
        
        # Parse Quiver Conviction
        macro_match = re.search(r'Macro:\s*([\d\.]+)', quiver_str)
        cat_match = re.search(r'Catalyst:\s*([\d\.]+)', quiver_str)
        macro_score = float(macro_match.group(1)) if macro_match else 0.0
        cat_score = float(cat_match.group(1)) if cat_match else 0.0
        quiver_total = macro_score + cat_score
        
        # Parse entries data
        ed = entries_data.get(ticker, {})
        win_rate = ed.get("win_rate_pct", 0.0)
        total_return = ed.get("total_return_pct", 0.0)
        
        if price > 0 and atr > 0:
            volatility = atr / price
            dmus.append({
                "ticker": ticker,
                "row_idx": i, # 0-indexed relative to the array
                "inputs": [volatility],
                "outputs": [quiver_total, win_rate, total_return]
            })

    if not dmus:
        print("No valid DMUs found.")
        return
        
    print(f"Parsed {len(dmus)} valid DMUs. Normalizing data...")

    # Build matrices
    n_dmus = len(dmus)
    X_raw = np.array([d["inputs"] for d in dmus], dtype=float)
    Y_raw = np.array([d["outputs"] for d in dmus], dtype=float)

    # Translation to handle negative outputs (like negative return)
    # y_trans = y - min(y) + 1.0 (if min < 0)
    for c in range(Y_raw.shape[1]):
        col_min = np.min(Y_raw[:, c])
        if col_min <= 0:
            Y_raw[:, c] = Y_raw[:, c] - col_min + 1.0
            
    # Add a tiny epsilon to anything that is exactly 0 to avoid LP failures
    X_raw[X_raw == 0] = 0.01
    Y_raw[Y_raw == 0] = 0.01

    # Normalize column-wise by the max value to improve numerical stability in the LP solver
    X_norm = X_raw / X_raw.max(axis=0)
    Y_norm = Y_raw / Y_raw.max(axis=0)

    n_inputs = X_norm.shape[1]
    n_outputs = Y_norm.shape[1]
    
    print("Running DEA (CCR Input-Oriented Multiplier Model)...")
    
    eps = 1e-6
    for o in range(n_dmus):
        c = np.concatenate([-Y_norm[o], np.zeros(n_inputs)])
        
        A_eq = np.concatenate([np.zeros(n_outputs), X_norm[o]]).reshape(1, -1)
        b_eq = [1.0]
        
        A_ub = np.hstack([Y_norm, -X_norm])
        b_ub = np.zeros(n_dmus)
        
        bounds = [(eps, None)] * (n_outputs + n_inputs)
        
        res = linprog(c, A_ub=A_ub, b_ub=b_ub, A_eq=A_eq, b_eq=b_eq, bounds=bounds, method='highs')
        
        if res.success:
            score = -res.fun
            # Cap between 0 and 1 just in case of rounding
            dmus[o]["dea_score"] = max(0.0, min(1.0, score))
        else:
            dmus[o]["dea_score"] = 0.0
            
    # Prepare batch update for Column K
    k_column = [[""] for _ in range(len(rows))]
    k_column[0] = ["DEA Score"]
    
    for d in dmus:
        score_100 = d["dea_score"] * 100.0
        k_column[d["row_idx"]] = [f"{score_100:.1f}"]
        print(f"{d['ticker']:>6} - DEA: {score_100:>5.1f} (Vol: {d['inputs'][0]*100:.1f}% | Q: {d['outputs'][0]:.1f} | WR: {d['outputs'][1]} | Ret: {d['outputs'][2]})")

    print("\nPushing DEA scores to Watchlist!K...")
    payload = json.dumps(k_column)
    run_gog(["update", LIVE_SHEET_ID, f"Watchlist!K1:K{len(rows)}", "--values-json", payload, "--input", "USER_ENTERED"])
    print("Successfully pushed to Google Sheets.")

if __name__ == "__main__":
    main()
