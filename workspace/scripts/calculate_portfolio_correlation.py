import json
import yfinance as yf
import pandas as pd
import numpy as np

# Load our active positions from trailing_radar.json
with open("/root/.openclaw/workspace/memory/trailing_radar.json", "r") as f:
    radar = json.load(f)

tickers = [t.replace(".", "-") for t in radar.keys()]

print(f"Fetching 90 days of closing price data for {len(tickers)} tickers...")
df = yf.download(tickers, period="90d", progress=False)['Close']

if df.empty:
    print("Error: Could not retrieve data from yfinance.")
    exit(1)

# Clean up column names to match our ticker format
df.columns = [c.replace("-", ".") for c in df.columns]

# Calculate correlation matrix
corr_matrix = df.corr()

# Find high correlation pairs (above 0.50)
pairs = []
for i in range(len(corr_matrix.columns)):
    for j in range(i + 1, len(corr_matrix.columns)):
        t1 = corr_matrix.columns[i]
        t2 = corr_matrix.columns[j]
        val = corr_matrix.iloc[i, j]
        pairs.append({"t1": t1, "t2": t2, "corr": val})

# Sort by correlation descending
pairs = sorted(pairs, key=lambda x: x["corr"], reverse=True)

print("\n=== TOP PORTFOLIO CORRELATIONS (>= 0.50) ===")
high_corr_count = 0
for p in pairs:
    if p["corr"] >= 0.50:
        print(f"  {p['t1']} <-> {p['t2']}: {p['corr']:.2f}")
        high_corr_count += 1
if high_corr_count == 0:
    print("  None found!")

print("\n=== NEGATIVE OR LOW CORRELATION HEDGES (< 0.0) ===")
hedge_count = 0
for p in pairs:
    if p["corr"] < 0.0:
        print(f"  {p['t1']} <-> {p['t2']}: {p['corr']:.2f}")
        hedge_count += 1
if hedge_count == 0:
    print("  None found!")

# Calculate average correlation for each ticker
avg_corrs = {}
for col in corr_matrix.columns:
    # Exclude correlation with itself (which is 1.0)
    avg_val = corr_matrix[col].drop(col).mean()
    avg_corrs[col] = avg_val

print("\n=== TICKER SYSTEMIC EXPOSURE (Average Correlation with Portfolio) ===")
sorted_avg = sorted(avg_corrs.items(), key=lambda x: x[1], reverse=True)
for ticker, avg in sorted_avg:
    print(f"  {ticker}: {avg:.2f}")

# Let's save the correlation matrix as a CSV so we have it if needed
corr_matrix.to_csv("/root/.openclaw/workspace/memory/portfolio_correlation_matrix.csv")
print("\nCorrelation matrix saved to /root/.openclaw/workspace/memory/portfolio_correlation_matrix.csv")
