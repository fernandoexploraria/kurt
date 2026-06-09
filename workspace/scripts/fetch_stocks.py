import yfinance as yf
import json
import sys

def fetch_stocks(tickers):
    results = {}
    for ticker in tickers:
        try:
            stock = yf.Ticker(ticker)
            info = stock.info
            hist = stock.history(period="5d")
            
            results[ticker] = {
                "currentPrice": info.get("currentPrice") or info.get("regularMarketPrice"),
                "trailingPE": info.get("trailingPE"),
                "fiftyTwoWeekHigh": info.get("fiftyTwoWeekHigh"),
                "fiveDayTrend": hist['Close'].tolist() if not hist.empty else []
            }
        except Exception as e:
            results[ticker] = {"error": str(e)}
    return results

if __name__ == "__main__":
    tickers = sys.argv[1:]
    print(json.dumps(fetch_stocks(tickers), indent=2))
