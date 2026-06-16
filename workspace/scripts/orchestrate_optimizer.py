#!/usr/bin/env python3
import json
import os
import subprocess
import time
from datetime import datetime

LIVE_SHEET_ID = "1kjzfc6uEzBFtmNjlU1x3TVbHuWPgY7jnNce8mNTe66I"
ACCOUNT = "fernando@exploraria.ai"
MULTIPLIERS_FILE = "/root/.openclaw/workspace/memory/optimized_multipliers.json"
ENTRIES_FILE = "/root/.openclaw/workspace/memory/optimized_entries.json"
WORKER_SCRIPT = "/root/.openclaw/workspace/scripts/optimize_multipliers.py"

# --- OPERATIONAL CONFIGURATION ---
PYTHON_EXEC = "/root/.openclaw/workspace/quant_env/bin/python3" # Secure virtual environment path [P0-1]
BATCH_SIZE = 5
REGIME_WINDOW_SEC = 43200 # 12 hours (Defines a fresh Sunday run window)

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
    print("Pulling master universe from Google Sheets (Positions + Watchlist)...")
    tickers = set()

    # Get Positions
    pos_data = run_gog(["get", LIVE_SHEET_ID, "Positions!A:A", "--json"])
    if pos_data and "values" in pos_data:
        for row in pos_data["values"]:
            if row and len(row) > 0 and row[0].strip() and row[0].strip() != "CASH" and row[0].strip().upper() != "TICKER":
                tickers.add(row[0].strip())

    # Get Watchlist
    watch_data = run_gog(["get", LIVE_SHEET_ID, "Watchlist!A:A", "--json"])
    if watch_data and "values" in watch_data:
        for row in watch_data["values"]:
            if row and len(row) > 0 and row[0].strip() and row[0].strip().upper() != "TICKER":
                tickers.add(row[0].strip())

    return list(tickers)

def clean_stale_state_files():
    """
    UPGRADE P0-8: Detects if this is a fresh Sunday run. If state files are older
    than 12 hours, clears them to prevent residual stale ticker pollution.
    """
    now = time.time()
    for path in [MULTIPLIERS_FILE, ENTRIES_FILE]:
        if os.path.exists(path):
            mtime = os.path.getmtime(path)
            if (now - mtime) > REGIME_WINDOW_SEC:
                print(f"[i] Stale last-week state file detected ({os.path.basename(path)}). Clearing for fresh Sunday optimization...")
                try:
                    os.remove(path)
                except Exception as e:
                    print(f"  [!] Failed to clear stale file: {e}")

def get_already_processed_tickers():
    """
    UPGRADE P0-8: Evaluates existing fresh files to support self-healing,
    one-click resumability in the event of mid-run crashes.
    """
    processed = set()
    # Only load keys if the file is fresh (modified within the last 12 hours)
    if os.path.exists(MULTIPLIERS_FILE):
        mtime = os.path.getmtime(MULTIPLIERS_FILE)
        if (time.time() - mtime) <= REGIME_WINDOW_SEC:
            try:
                with open(MULTIPLIERS_FILE, 'r') as f:
                    data = json.load(f)
                for ticker in data.keys():
                    processed.add(ticker)
                print(f"[i] Fresh state file found. Resuming run. Detected {len(processed)} already optimized tickers.")
            except:
                pass
    return processed

def main():
    print(f"[{datetime.now().isoformat()}] Starting Master Quant Orchestrator...")

    # 1. Clear state files from previous weeks to prevent pollution
    clean_stale_state_files()

    # 2. Retrieve the active target universe
    universe = get_master_universe()
    if not universe:
        print("Failed to pull universe. Exiting.")
        return 1

    # 3. Filter out completed tickers if resuming a crashed run from earlier today
    processed = get_already_processed_tickers()
    tickers_to_process = [t for t in universe if t not in processed]

    if not tickers_to_process:
        print("🎉 SUCCESS: All master universe tickers are already optimized and up-to-date for today!")
        return 0

    print(f"Total Universe: {len(universe)} | Already Completed: {len(processed)} | Remaining: {len(tickers_to_process)}")

    # 4. Chunk remaining tickers into batches of 5
    chunks = [tickers_to_process[i:i + BATCH_SIZE] for i in range(0, len(tickers_to_process), BATCH_SIZE)]
    print(f"Divided remaining tasks into {len(chunks)} execution batches.\n")

    for idx, chunk in enumerate(chunks):
        print(f"=== Dispatching Batch {idx + 1}/{len(chunks)}: {chunk} ===")

        # Secure, list-based execution targeting the correct virtual environment path [P0-1, P0-4]
        cmd = [PYTHON_EXEC, WORKER_SCRIPT] + chunk

        try:
            # Blocks and waits for the worker batch to cleanly complete
            subprocess.run(cmd, check=True, shell=False)
            print(f"=== Batch {idx + 1} Completed Successfully ===\n")
        except subprocess.CalledProcessError as e:
            print(f"!!! Error in Batch {idx + 1}. Worker exited with code {e.returncode}. Continuing to next batch...\n")

    print(f"[{datetime.now().isoformat()}] Master Orchestration Complete! Active universe synchronized.")
    return 0

if __name__ == "__main__":
    import sys
    sys.exit(main())
