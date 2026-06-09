import json
import os
import sys
sys.path.append('/root/.openclaw/workspace/scripts')
import schwab_client
import subprocess
from datetime import datetime

QUEUE_FILE = "/root/.openclaw/workspace/memory/execution_queue.json"
HISTORY_FILE = "/root/.openclaw/workspace/memory/execution_history.json"
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
    else:
        print(f"GOG Error: {result.stderr}")
        return None

def process_buy(order_id, order):
    ticker = order["ticker"]
    shares_to_buy = int(order["shares"])
    price = float(order["execution_price"])
    total_cost = shares_to_buy * price
    order_type = order.get("order_type", "UNKNOWN")
    
    print(f"Validating BUY for {shares_to_buy} shares of {ticker}...")
    positions_data = run_gog(f'get {LIVE_SHEET_ID} "Positions!A1:F100"')
    
    if not positions_data or "values" not in positions_data:
        print("Failed to fetch positions.")
        return False
        
    rows = positions_data["values"]
    ticker_row_idx = -1
    current_shares = 0
    current_avg_cost = 0.0
    cash_row_idx = -1
    current_cash = 0.0

    for i, row in enumerate(rows):
        if not row: continue
        sheet_row_num = i + 1 
        if row[0] == "CASH":
            cash_row_idx = sheet_row_num
            current_cash = float(row[2])
        elif row[0] == ticker:
            ticker_row_idx = sheet_row_num
            current_shares = int(row[1])
            current_avg_cost = float(row[2]) if row[2] else 0.0

    if current_cash < total_cost:
        print(f"Validation Failed: Insufficient funds. Cost: ${total_cost:.2f}, Cash: ${current_cash:.2f}")
        return False

    print("Validation passed. Proceeding with execution loop...")

    note = f"Automated BUY via {order_type}. Source: {order.get('source', 'System')}."
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # --- SCHWAB PREVIEW INJECTION ---
    print("Previewing order with Schwab API...")
    preview = schwab_client.preview_order("BUY", ticker, shares_to_buy, order_type, price)
    sent_payload = json.dumps(preview.get("sent_payload", {}))
    schwab_resp = json.dumps(preview.get("schwab_response", {}))

    # Escape quotes for bash JSON payload (double quotes are inside the json strings)
    # We replace single quotes with '"'"' to safely wrap the entire string in bash single quotes.
    sent_payload_bash = sent_payload.replace("'", "'\"'\"'")
    schwab_resp_bash = schwab_resp.replace("'", "'\"'\"'")

    row_data = [timestamp, "BUY", ticker, str(shares_to_buy), f"{price:.2f}", f"{total_cost:.2f}", note, sent_payload_bash, schwab_resp_bash]
    ledger_payload = json.dumps([row_data])
    # The ledger_payload itself needs to be escaped for the bash command
    ledger_payload_escaped = ledger_payload.replace("'", "'\"'\"'")
    # --------------------------------

    print("Writing to Ledger...")
    run_gog(f"append {LIVE_SHEET_ID} \"Transactions!A:I\" --values-json '{ledger_payload_escaped}' --insert INSERT_ROWS")

    new_cash = current_cash - total_cost
    cash_payload = f'[["{new_cash:.2f}", "{new_cash:.2f}", "{new_cash:.2f}"]]'
    print(f"Updating Cash from ${current_cash:.2f} to ${new_cash:.2f}...")
    run_gog(f"update {LIVE_SHEET_ID} \"Positions!C{cash_row_idx}:E{cash_row_idx}\" --values-json '{cash_payload}' --input USER_ENTERED")

    if ticker_row_idx != -1:
        if current_shares > 0:
            print("Branch A: Updating existing active position...")
            new_shares = current_shares + shares_to_buy
            old_basis = current_shares * current_avg_cost
            new_basis = old_basis + total_cost
            new_avg_cost = new_basis / new_shares
            
            asset_payload = f'[["{new_shares}", "{new_avg_cost:.2f}"]]'
            run_gog(f"update {LIVE_SHEET_ID} \"Positions!B{ticker_row_idx}:C{ticker_row_idx}\" --values-json '{asset_payload}' --input USER_ENTERED")
        else:
            print("Branch B: Updating zeroed-out position...")
            asset_payload = f'[["{shares_to_buy}", "{price:.2f}"]]'
            run_gog(f"update {LIVE_SHEET_ID} \"Positions!B{ticker_row_idx}:C{ticker_row_idx}\" --values-json '{asset_payload}' --input USER_ENTERED")
    else:
        print("Branch C: Creating brand new asset row...")
        formula_price = f'=GOOGLEFINANCE(INDIRECT("A" & ROW()))'
        formula_value = f'=INDIRECT("B" & ROW()) * INDIRECT("D" & ROW())'
        formula_pl = f'=INDIRECT("E" & ROW()) - (INDIRECT("B" & ROW()) * INDIRECT("C" & ROW()))'
        
        f_p = formula_price.replace('"', '\\"')
        f_v = formula_value.replace('"', '\\"')
        f_pl = formula_pl.replace('"', '\\"')
        
        new_row_payload = f'[["{ticker}", "{shares_to_buy}", "{price:.2f}", "{f_p}", "{f_v}", "{f_pl}"]]'
        run_gog(f"append {LIVE_SHEET_ID} \"Positions!A:F\" --values-json '{new_row_payload}' --insert INSERT_ROWS")

    print(f"SUCCESS: BUY Order {order_id} fully executed.")
    return True

def process_sell(order_id, order):
    ticker = order["ticker"]
    shares_to_sell = int(order["shares"])
    price = float(order["execution_price"])
    total_value = shares_to_sell * price
    order_type = order.get("order_type", "UNKNOWN")
    
    print(f"Validating SELL for {shares_to_sell} shares of {ticker}...")
    positions_data = run_gog(f'get {LIVE_SHEET_ID} "Positions!A1:F100"')
    
    if not positions_data or "values" not in positions_data:
        print("Failed to fetch positions.")
        return False
        
    rows = positions_data["values"]
    ticker_row_idx = -1
    current_shares = 0
    cash_row_idx = -1
    current_cash = 0.0

    for i, row in enumerate(rows):
        if not row: continue
        sheet_row_num = i + 1 
        if row[0] == "CASH":
            cash_row_idx = sheet_row_num
            current_cash = float(row[2])
        elif row[0] == ticker:
            ticker_row_idx = sheet_row_num
            current_shares = int(row[1])

    if ticker_row_idx == -1:
        print(f"Validation Failed: {ticker} not found in Positions.")
        return False
        
    if current_shares < shares_to_sell:
        print(f"Validation Failed: Tried to sell {shares_to_sell} {ticker}, but only own {current_shares}.")
        return False

    print("Validation passed. Proceeding with execution loop...")

    note = f"Automated SELL via {order_type}. Source: {order.get('source', 'System')}."
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # --- SCHWAB PREVIEW INJECTION ---
    print("Previewing order with Schwab API...")
    preview = schwab_client.preview_order("SELL", ticker, shares_to_sell, order_type, price)
    sent_payload = json.dumps(preview.get("sent_payload", {}))
    schwab_resp = json.dumps(preview.get("schwab_response", {}))

    sent_payload_bash = sent_payload.replace("'", "'\"'\"'")
    schwab_resp_bash = schwab_resp.replace("'", "'\"'\"'")

    row_data = [timestamp, "SELL", ticker, str(shares_to_sell), f"{price:.2f}", f"{total_value:.2f}", note, sent_payload_bash, schwab_resp_bash]
    ledger_payload = json.dumps([row_data])
    ledger_payload_escaped = ledger_payload.replace("'", "'\"'\"'")
    # --------------------------------

    print("Writing to Ledger...")
    run_gog(f"append {LIVE_SHEET_ID} \"Transactions!A:I\" --values-json '{ledger_payload_escaped}' --insert INSERT_ROWS")

    new_cash = current_cash + total_value
    cash_payload = f'[["{new_cash:.2f}", "{new_cash:.2f}", "{new_cash:.2f}"]]'
    print(f"Updating Cash from ${current_cash:.2f} to ${new_cash:.2f}...")
    run_gog(f"update {LIVE_SHEET_ID} \"Positions!C{cash_row_idx}:E{cash_row_idx}\" --values-json '{cash_payload}' --input USER_ENTERED")

    new_shares = current_shares - shares_to_sell
    print(f"Updating Asset: {ticker} shares from {current_shares} to {new_shares}...")
    
    if new_shares == 0:
        run_gog(f"update {LIVE_SHEET_ID} \"Positions!B{ticker_row_idx}:C{ticker_row_idx}\" --values-json '[[\"0\", \"0.00\"]]' --input USER_ENTERED")
    else:
        run_gog(f"update {LIVE_SHEET_ID} \"Positions!B{ticker_row_idx}\" --values-json '[[\"{new_shares}\"]]' --input USER_ENTERED")

    print(f"SUCCESS: SELL Order {order_id} fully executed.")
    return True

def main():
    if not os.path.exists(QUEUE_FILE):
        print("NO_REPLY")
        return

    with open(QUEUE_FILE, 'r') as f:
        try:
            queue = json.load(f)
        except:
            print("NO_REPLY")
            return

    if not queue:
        print("NO_REPLY")
        return

    # Phase 1: Pre-Flight Lock
    locked_orders = []
    needs_save = False
    
    for order_id, order in queue.items():
        if order.get("status") == "pending":
            order["status"] = "processing"
            locked_orders.append(order_id)
            needs_save = True

    if not locked_orders:
        # Queue is empty or only contains failed/stuck processing orders
        print("NO_REPLY")
        return

    if needs_save:
        with open(QUEUE_FILE, 'w') as f:
            json.dump(queue, f, indent=2)
            
    print(f"Locked {len(locked_orders)} orders for processing. Pre-flight lock engaged.")

    # Phase 2: Execution Loop
    history = {}
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, 'r') as f:
            try:
                history = json.load(f)
            except:
                pass

    orders_to_remove = []

    for order_id in locked_orders:
        order = queue[order_id]
        action = order.get("action")
        success = False
        
        if action == "SELL":
            success = process_sell(order_id, order)
        elif action == "BUY":
            success = process_buy(order_id, order)
            
        if success:
            order["status"] = "completed"
            order["executed_at"] = datetime.now().isoformat()
            history[order_id] = order
            orders_to_remove.append(order_id)
        else:
            order["status"] = "failed"
            # We don't remove failed orders, they stay in queue for manual audit

    # Phase 3: Queue Cleanup (Post-Flight)
    if orders_to_remove or any(o.get("status") == "failed" for o in queue.values()):
        for oid in orders_to_remove:
            del queue[oid]
            
        with open(QUEUE_FILE, 'w') as f:
            json.dump(queue, f, indent=2)
            
        with open(HISTORY_FILE, 'w') as f:
            json.dump(history, f, indent=2)

if __name__ == "__main__":
    main()
