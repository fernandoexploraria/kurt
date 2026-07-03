import json
import os
import time

state_path = '/root/.openclaw/workspace/memory/heartbeat-state.json'
try:
    with open(state_path, 'r') as f:
        state = json.load(f)
except Exception:
    state = {"lastChecks": {}}

state["lastChecks"]["portfolio_alert"] = int(time.time())

os.makedirs(os.path.dirname(state_path), exist_ok=True)
with open(state_path, 'w') as f:
    json.dump(state, f)
