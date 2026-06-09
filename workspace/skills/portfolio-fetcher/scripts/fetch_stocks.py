import yfinance as yf
import json

def get_stock_data(tickers):
    results = {}
    for ticker in tickers:
        try:
            t = yf.Ticker(ticker)
            info = t.info
            hist = t.history(period="5d")
            trend = hist['Close'].tolist() if not hist.empty else []
            results[ticker] = {
                "currentPrice": info.get("currentPrice"),
                "trailingPE": info.get("trailingPE"),
                "fiftyTwoWeekHigh": info.get("fiftyTwoWeekHigh"),
                "fiveDayTrend": trend
            }
        except Exception as e:
            results[ticker] = {"error": str(e)}
    return results

if __name__ == "__main__":
    import sys
    args = sys.argv[1:]
    tickers = args if args else ["CRM", "MSFT"]
    data = get_stock_data(tickers)
    print(json.dumps(data, indent=2))
