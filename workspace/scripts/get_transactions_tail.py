import os
import subprocess
import json

ACCOUNT = "fernando@exploraria.ai"
LIVE_SHEET_ID = "1kjzfc6uEzBFtmNjlU1x3TVbHuWPgY7jnNce8mNTe66I"

def main():
    env = os.environ.copy()
    env["GOG_ACCOUNT"] = ACCOUNT
    cmd = ["/root/.openclaw/workspace/gog", "sheets", "get", LIVE_SHEET_ID, "Transactions!A:G", "--json"]
    r = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if r.returncode != 0:
        print("Error:", r.stderr)
        return
    try:
        data = json.loads(r.stdout.strip())
        values = data.get("values", [])
        print("Total rows:", len(values))
        print("Latest 20 rows:")
        for i, row in enumerate(values[-20:]):
            print(f"{len(values)-20+i}: {row}")
    except Exception as e:
        print("Failed to parse JSON:", e)
        print("Raw stdout sample:", r.stdout[:500])

if __name__ == "__main__":
    main()
