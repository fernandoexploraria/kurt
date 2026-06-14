#!/usr/bin/env python3
import json
import subprocess
import os
import numpy as np
from scipy.optimize import linprog

LIVE_SHEET_ID = "1kjzfc6uEzBFtmNjlU1x3TVbHuWPgY7jnNce8mNTe66I"
ACCOUNT = "fernando@exploraria.ai"
ENTRIES_FILE = "/root/.openclaw/workspace/memory/optimized_entries.json"
SHIELD_FILE = "/root/.openclaw/workspace/memory/quiver_shield.json"

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

def load_json(path):
    if os.path.exists(path):
        try:
            with open(path, 'r') as f:
                return json.load(f)
        except: pass
    return {}

def main():
    print("=== DEA Prototype for Equity Selection (BCC Input-Oriented) ===")

    # 1. Load data
    print("Loading optimized entries...")
    entries_data = load_json(ENTRIES_FILE)

    # Decoupled Ingestion: Load raw sentiment directly from database [P3-10]
    print("Loading Quiver Shield conviction database...")
    shield_data = load_json(SHIELD_FILE)

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
        if i == 0 or len(row) == 0: continue
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

        # Direct Database lookup: Extract raw, unformatted scores safely
        shield_entry = shield_data.get(ticker, {})
        macro_score = float(shield_entry.get("score", 50.0))
        cat_score = float(shield_entry.get("catalyst_score", 0.0))
        quiver_total = macro_score + cat_score

        # Parse entries data
        ed = entries_data.get(ticker, {})
        win_rate = ed.get("win_rate_pct", 0.0)
        total_return = ed.get("total_return_pct", 0.0)

        if price > 0 and atr > 0:
            volatility = atr / price
            dmus.append({
                "ticker": ticker,
                "row_idx": i,
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

    # Translation to handle negative outputs (mathematically valid in BCC)
    for c in range(Y_raw.shape[1]):
        col_min = np.min(Y_raw[:, c])
        if col_min <= 0:
            Y_raw[:, c] = Y_raw[:, c] - col_min + 1.0

    # Add a tiny epsilon to prevent LP singular matrices or division-by-zero
    X_raw[X_raw == 0] = 0.01
    Y_raw[Y_raw == 0] = 0.01

    # Normalize column-wise to stabilize the simplex/highs solver
    X_norm = X_raw / X_raw.max(axis=0)
    Y_norm = Y_raw / Y_raw.max(axis=0)

    n_inputs = X_norm.shape[1]
    n_outputs = Y_norm.shape[1]

    print("Running DEA (BCC Input-Oriented Multiplier Model)...")

    eps = 1e-6
    for o in range(n_dmus):
        # We want to maximize sum(u_r * y_ro) - u_0, which is equivalent to
        # minimizing -sum(u_r * y_ro) + u_0.
        # Variable vector structure: w = [u_1,..., u_s, v_1,..., v_m, u_0]
        c = np.concatenate([-Y_norm[o], np.zeros(n_inputs), [1.0]])

        # Equality constraint: sum(v_i * x_io) = 1
        # In matrix: [0,..., 0, x_1o,..., x_mo, 0] * w = 1
        A_eq = np.concatenate([np.zeros(n_outputs), X_norm[o], [0.0]]).reshape(1, -1)
        b_eq = [1.0]

        # Inequality constraint: sum(u_r * y_rj) - sum(v_i * x_ij) - u_0 <= 0
        # In matrix: [Y_norm, -X_norm, -1] * w <= 0
        u0_col = -np.ones((n_dmus, 1))
        A_ub = np.hstack([Y_norm, -X_norm, u0_col])
        b_ub = np.zeros(n_dmus)

        # Bounds: u_r >= eps, v_i >= eps, u_0 is free (None, None)
        bounds = [(eps, None)] * (n_outputs + n_inputs) + [(None, None)]

        res = linprog(c, A_ub=A_ub, b_ub=b_ub, A_eq=A_eq, b_eq=b_eq, bounds=bounds, method='highs')

        if res.success:
            # Optimal score is the negative of the minimized objective value
            score = -res.fun
            dmus[o]["dea_score"] = max(0.0, min(1.0, score))
        else:
            dmus[o]["dea_score"] = 0.0

    # Prepare batch update for Column K (Watchlist!K)
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
