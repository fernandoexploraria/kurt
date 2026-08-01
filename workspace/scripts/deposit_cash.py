import argparse
import json
import os
import subprocess
from datetime import datetime

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

def main():
    parser = argparse.ArgumentParser(description="Inject a cash deposit/withdrawal into the simulator sheet")
    parser.add_argument("--amount", required=True, type=float, help="The amount of cash to add (positive) or remove (negative)")
    parser.add_argument("--notes", default="Cash Deposit", help="Description of the transaction")
    args = parser.parse_args()

    amount = args.amount
    action_type = "DEPOSIT" if amount >= 0 else "WITHDRAWAL"
    abs_amount = abs(amount)

    print(f"Retrieving current CASH balance from Google Sheets...")
    positions_data = run_gog(f'get {LIVE_SHEET_ID} "Positions!A1:F100"')
    
    if not positions_data or "values" not in positions_data:
        print("Error: Failed to fetch positions.")
        return

    rows = positions_data["values"]
    cash_row_idx = -1
    current_cash = 0.0

    for i, row in enumerate(rows):
        if not row: continue
        sheet_row_num = i + 1 
        if row[0] == "CASH":
            cash_row_idx = sheet_row_num
            current_cash = float(row[2])
            break

    if cash_row_idx == -1:
        print("Error: CASH row not found in Positions sheet.")
        return

    new_cash = current_cash + amount
    print(f"Current Cash: ${current_cash:.2f}")
    print(f"New Cash: ${new_cash:.2f}")

    # Step 1: Update Cash row Columns C, D, and E
    cash_payload = f'[["{new_cash:.2f}", "{new_cash:.2f}", "{new_cash:.2f}"]]'
    print(f"Updating Positions sheet (Row {cash_row_idx})...")
    update_res = run_gog(f"update {LIVE_SHEET_ID} \"Positions!C{cash_row_idx}:E{cash_row_idx}\" --values-json '{cash_payload}' --input USER_ENTERED")
    if not update_res:
        print("Error updating CASH balance.")
        return

    # Step 2: Log transaction in Transactions sheet
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    row_data = [timestamp, action_type, "CASH", "1", f"{abs_amount:.2f}", f"{abs_amount:.2f}", args.notes]
    ledger_payload = json.dumps([row_data])
    ledger_payload_escaped = ledger_payload.replace("'", "'\"'\"'")

    print("Appending to Transactions ledger...")
    append_res = run_gog(f"append {LIVE_SHEET_ID} \"Transactions!A:I\" --values-json '{ledger_payload_escaped}' --insert INSERT_ROWS")
    if not append_res:
        print("Warning: Cash updated, but failed to write ledger entry.")
    else:
        print("SUCCESS: Transaction logged in Transactions sheet.")

    print(f"\n✅ CASH transaction successfully processed!")
    print(f"Action: {action_type}")
    print(f"Amount: ${abs_amount:.2f}")
    print(f"Notes: {args.notes}")
    print(f"Previous Balance: ${current_cash:.2f} -> New Balance: ${new_cash:.2f}")

if __name__ == "__main__":
    main()
