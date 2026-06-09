# OpenClaw Trading Architecture V4: The Broker-Native Transition

This document serves as the official blueprint for transitioning the "Simulator V3" architecture into a fully institutional, broker-native quantitative system (V4). 

The overarching goal of V4 is to offload micro-second execution and ratcheting to Charles Schwab's mainframe while transitioning OpenClaw into a pure "Quant Brain" (calculating targets) and "Ledger Sync" (reconciling accounting).

## Phase 1: The Execution Hand-Off
**Goal:** Eliminate local 1-minute execution latency by moving all Limit Orders natively to the exchange.
* **Retire Local Snipers:** Delete the 1-Minute `buy_limit_sniper.py` cron job.
* **Direct Routing:** Update manual trap commands (e.g., `add_pending_order.py`) to bypass the local `pending_orders.json` file. Instead, they will fire raw `GTC LIMIT` order payloads directly to the Schwab API.
* **Advanced Routing:** Unlocks the ability to use complex multi-leg orders (e.g., OCO Brackets) directly on the exchange.

## Phase 2: The Nightly Quant Brain (Dynamic Trailing Stops)
**Goal:** Offload the manual trailing stop check to Schwab's tick-by-tick servers, while keeping the math dynamic.
* **Retire Local Trailing:** Delete the 1-Minute `intraday_sniper.py` cron job.
* **Upgrade `build_radar.py`:** At 1:00 AM, this script will:
  1. Cancel all active trailing stops on Schwab.
  2. Recalculate the optimal ATR (Volatility) drop amount for high-beta stocks.
  3. Send brand new Native `TRAILING_STOP` payloads to Schwab for the upcoming trading session.

## Phase 3: The Ledger Sync (The Watermark Method)
**Goal:** Create a bulletproof accounting system that only reacts to true broker receipts.
* **Retire Local Executions:** Delete `process_executions.py`.
* **Build `ledger_sync.py`:** A new cron job running every 15-30 minutes.
  * Polls the Schwab `/orders` endpoint filtering for `"status": "FILLED"`.
  * Cross-references a local `processed_receipts.json` (Watermark Cache) to find the delta (newly executed trades).
  * Appends the exact execution price, shares, and timestamp to the Google Sheet `Transactions` tab.

## Phase 4: Event Sourcing (The Self-Healing Portfolio)
**Goal:** Eliminate manual spreadsheet errors by making the `Transactions` tab the Single Source of Truth.
* **Build `reconcile_ledger.py`:** Runs nightly at 11:00 PM.
  * Reads the `Transactions` tab from top to bottom (the beginning of time to present).
  * Mathematically calculates the exact running Cash Balance, Share Counts, and Average Cost Basis.
  * Completely overwrites the `Positions` tab from scratch to guarantee 100% mathematical perfection.

## Phase 5: Alternative Data & Situational Awareness
**Goal:** Maximize the "Alpha" generation and market context.
* **Corporate Alpha:** Upgrade Quiver Quantitative to the "Trader Tier" to unlock Corporate Insider buying data (CEOs, CFOs). Inject this into the Full Arsenal as **Layer 4d**.
* **Market Weather:** Activate the 12:05 PM CST Macro Pulse (querying Schwab for real-time `$VIX` and `$TICK` data) to read the institutional mood of the market.

## Phase 6: D-Day (The "Clone & Cleanse" Fund Migration)
**Goal:** Transition from simulated tracking to 1:1 Live Execution parity with the Schwab brokerage account, without losing the dashboard UI or blending "paper" P&L with "live" P&L.
* **Step 1 (The Clone):** Duplicate the entire Google Sheet. The original file becomes the frozen "Simulator V3" archive. The new file becomes "Live V4".
* **Step 2 (The Cleanse - UI):** 
  * In the new `Transactions` tab, delete all rows except the first. Edit that row to reflect a pure `DEPOSIT` of `$20,000` CASH (The Genesis Block).
  * In the `Positions` tab, delete all asset rows, leaving only the `CASH` row set to `$20,000`.
* **Step 3 (The Cleanse - Engine):**
  * Update all Python scripts to point to the new Google Sheet ID.
  * Delete all local state files (`pending_orders.json`, `trailing_radar.json`, `snag_state.json`, `execution_queue.json`, `execution_history.json`) to give the AI engine complete amnesia of the simulation.
* **Step 4 (The Rebirth):** Deposit live funds into Schwab, manually execute initial D-Day purchases via the API, and allow the new V4 `ledger_sync.py` and Event Sourcing bots to autonomously rebuild the `Positions` tab with perfect 1:1 broker math. The nightly Quant crons will automatically restore all ATR/Beta math.
