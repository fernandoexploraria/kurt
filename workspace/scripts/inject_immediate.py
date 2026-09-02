import argparse
import fcntl
import json
import os
import time
import uuid

QUEUE_FILE = "/root/.openclaw/workspace/memory/execution_queue.json"

def main():
    parser = argparse.ArgumentParser(description="Inject IMMEDIATE orders into execution queue")
    parser.add_argument("--ticker", required=True, help="Stock ticker symbol")
    parser.add_argument("--action", required=True, choices=["BUY", "SELL"], help="BUY or SELL")
    parser.add_argument("--shares", required=True, type=int, help="Number of shares")
    parser.add_argument("--price", required=True, type=float, help="Execution/Target price")
    args = parser.parse_args()

    os.makedirs(os.path.dirname(QUEUE_FILE), exist_ok=True)
    
    order_id = f"IMMED_{int(time.time())}_{args.ticker}_{str(uuid.uuid4())[:8]}"
    
    with open(QUEUE_FILE, "a+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0)
        try:
            content = f.read()
            queue = json.loads(content) if content.strip() else {}
        except:
            queue = {}

        queue[order_id] = {
            "ticker": args.ticker.upper(),
            "action": args.action.upper(),
            "shares": args.shares,
            "execution_price": args.price,
            "order_type": "IMMEDIATE",
            "source": "Telegram Chat",
            "status": "pending",
            "timestamp": int(time.time())
        }

        f.seek(0)
        f.truncate()
        json.dump(queue, f, indent=2)
        
    print(f"SUCCESS: Injected {args.action} {args.shares} shares of {args.ticker} at ${args.price} into queue as {order_id}")

if __name__ == "__main__":
    main()
