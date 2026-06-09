---
name: portfolio-fetcher
description: Fetch real-time stock price, P/E ratio, 52-week high, and 5-day price trends for specific tickers. Use this when the user needs quick financial metrics or when preparing data for portfolio management.
---

# Portfolio Fetcher

This skill provides a fast way to retrieve key stock metrics using a local Python script powered by `yfinance`.

## Workflow

1.  **Identify Tickers**: Determine the stock tickers requested (e.g., AAPL, MSFT, CRM).
2.  **Execute Script**: Run the bundled script `scripts/fetch_stocks.py` with the tickers as arguments.
3.  **Process Output**: The script returns a JSON object containing `currentPrice`, `trailingPE`, `fiftyTwoWeekHigh`, and `fiveDayTrend`.

## Example Usage

```bash
python3 scripts/fetch_stocks.py AAPL MSFT
```

## Troubleshooting

- If a ticker returns an error, verify the ticker symbol on Yahoo Finance.
- Ensure `yfinance` is installed in the environment.
