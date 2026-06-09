---
name: portfolio-auditor
description: Calculate the performance and "Decision Alpha" of trades against the S&P 500 benchmark.
---

# Portfolio Auditor

This skill allows you to calculate the performance and "Decision Alpha" of Fer's trades against the S&P 500 benchmark to prove the value of the active trading strategy.

## How to execute this skill:

1. Use your `gog` tool to read the 'Transactions' tab from the "Simulator V3" Google Sheet.
2. Extract the Ticker, Entry Price, and Entry Date for the trades requested by Fer.
3. Format the extracted data into a strict JSON string array like this: `[{"ticker": "NVDA", "entry_price": 90.00, "date": "2024-04-15"}]`
4. Run the python engine in your terminal by passing the JSON string as the exact argument:
 `python3 skills/portfolio-auditor/audit_report.py '<YOUR_JSON_STRING>'`
5. Present the resulting markdown report directly to Fer. Do not alter or summarize the mathematical output; give him the raw report.
