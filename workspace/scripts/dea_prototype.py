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

    # Add a tiny epsilon to prevent LP singular matrices or division-by-zero
    X_raw[X_raw == 0] = 0.01
    Y_raw[Y_raw == 0] = 0.01

    # Normalize column-wise to stabilize the simplex/highs solver
    X_norm = X_raw / X_raw.max(axis=0)
    Y_norm = Y_raw / Y_raw.max(axis=0)

    # Calculate global ranges on the NORMALIZED coordinates to prevent scaling distortions
    Rx = np.ptp(X_norm, axis=0)
    Rx[Rx == 0] = 1.0  # Prevent division by zero
    Ry = np.ptp(Y_norm, axis=0)
    Ry[Ry == 0] = 1.0

    n_inputs = X_norm.shape[1]
    n_outputs = Y_norm.shape[1]

    print("Running DEA (RDM/Directional Distance with Cross-Efficiency)...")

    eps = 1e-6
    Weights = np.zeros((n_dmus, n_inputs + n_outputs + 1))

    # Phase 1: Self-Evaluation (Solve LP for each DMU using normalized coordinates)
    for o in range(n_dmus):
        # Objective: Minimize inefficiency beta = sum(v*x_o) - sum(u*y_o) + u_0 on normalized data
        c = np.concatenate([X_norm[o], -Y_norm[o], [1.0]])

        # Equality constraint: sum(v*Rx) + sum(u*Ry) = 1 (Normalizes weights globally)
        A_eq = np.concatenate([Rx, Ry, [0.0]]).reshape(1, -1)
        b_eq = [1.0]

        # Inequality constraint: sum(v*x_j) - sum(u*y_j) + u_0 >= 0 for all j
        # Scipy linprog uses A_ub * w <= b_ub, so multiply by -1:
        # -sum(v*x_j) + sum(u*y_j) - u_0 <= 0
        u0_col = -np.ones((n_dmus, 1))
        A_ub = np.hstack([-X_norm, Y_norm, u0_col])
        b_ub = np.zeros(n_dmus)

        # Bounds: v_i >= eps, u_r >= eps, u_0 is free
        bounds = [(eps, None)] * (n_inputs + n_outputs) + [(None, None)]

        res = linprog(c, A_ub=A_ub, b_ub=b_ub, A_eq=A_eq, b_eq=b_eq, bounds=bounds, method='highs')

        if res.success:
            Weights[o, :] = res.x
            dmus[o]["self_score"] = max(0.0, 1.0 - res.fun)
        else:
            Weights[o, :] = 0
            dmus[o]["self_score"] = 0.0

    # Phase 2: Cross-Efficiency Matrix (Evaluated on normalized coordinates)
    print("Calculating Cross-Efficiency Peer Evaluation Matrix...")
    CE_matrix = np.zeros((n_dmus, n_dmus))

    for o in range(n_dmus):
        v = Weights[o, :n_inputs]
        u = Weights[o, n_inputs:n_inputs+n_outputs]
        u0 = Weights[o, -1]
        
        for j in range(n_dmus):
            # Inefficiency of DMU j evaluated using DMU o's optimal weights
            inefficiency = np.dot(X_norm[j], v) - np.dot(Y_norm[j], u) + u0
            # Cross-Efficiency score clipped to a minimum of 0.0 [P0-8]
            CE_matrix[o, j] = max(0.0, 1.0 - inefficiency)

    # Phase 3: Aggregate Cross-Efficiency
    for j in range(n_dmus):
        # Average of how all peers scored DMU j
        avg_ce = np.mean(CE_matrix[:, j])
        dmus[j]["dea_score"] = max(0.0, min(1.0, avg_ce))

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

    from datetime import datetime
    # Prepare local cache payload [P0-6]
    dea_scores = {}
    for d in dmus:
        dea_scores[d["ticker"]] = {
            "dea_score": round(d["dea_score"], 4),
            "last_updated": datetime.now().isoformat()
        }

    # Save local JSON database atomically using our temp-file replace pattern [P0-6]
    DEA_SCORES_FILE = "/root/.openclaw/workspace/memory/dea_scores.json"
    tmp_path = DEA_SCORES_FILE + ".tmp"
    with open(tmp_path, 'w') as f:
        json.dump(dea_scores, f, indent=2)
    os.replace(tmp_path, DEA_SCORES_FILE)
    print("Local DEA scores database updated.")

if __name__ == "__main__":
    main()
