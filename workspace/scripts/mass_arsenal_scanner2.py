import json
import subprocess

LIVE_SHEET_ID = "1kjzfc6uEzBFtmNjlU1x3TVbHuWPgY7jnNce8mNTe66I"
ACCOUNT = "fernando@exploraria.ai"

def run_gog(cmd):
    full_cmd = f"GOG_ACCOUNT={ACCOUNT} gog sheets {cmd}"
    res = subprocess.run(full_cmd, shell=True, capture_output=True, text=True)
    try:
        return json.loads(res.stdout.strip())
    except:
        return None

wl = run_gog(f'get {LIVE_SHEET_ID} "Watchlist!A:K" --json')
candidates = {}
if wl and "values" in wl:
    for i, row in enumerate(wl["values"]):
        if i == 0 or not row: continue
        ticker = row[0].strip()
        if not ticker or ticker == "CASH": continue
        try:
            candidates[ticker] = {
                "price": float(str(row[2]).replace('$', '').replace(',', '')),
                "dist": float(str(row[4]).replace('%', '')),
                "quiver": str(row[9]).split('|')[0].strip() if len(row) > 9 else "N/A"
            }
        except:
            pass

sorted_c = sorted(candidates.items(), key=lambda x: x[1]['dist'])
for t, d in sorted_c[5:10]:
    print(f"{t}: {d['dist']}% away | Quiver: {d['quiver']}")
