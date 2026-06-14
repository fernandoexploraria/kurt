import subprocess
import os
import json
ACCOUNT = "fernando@exploraria.ai"
def run_gog(args_list):
    env = os.environ.copy()
    env["GOG_ACCOUNT"] = ACCOUNT
    cmd_list = ["gog", "sheets"] + args_list
    print(f"Running: {cmd_list}")
    result = subprocess.run(cmd_list, env=env, shell=False, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Error: {result.stderr}")
        return None
    try:
        return json.loads(result.stdout.strip())
    except Exception as e:
        print(f"JSON Error: {e}")
        return None

out = run_gog(["get", "1kjzfc6uEzBFtmNjlU1x3TVbHuWPgY7jnNce8mNTe66I", "Watchlist!A:K", "--json"])
if out:
    print("Success")
else:
    print("Failed")
