import yfinance as yf
import json
import sys

args = sys.argv[1:]
ticker_sym = args[0] if args else 'AFL'

t = yf.Ticker(ticker_sym)
info = t.info

res = {
    "targetMeanPrice": info.get("targetMeanPrice"),
    "targetHighPrice": info.get("targetHighPrice"),
    "targetLowPrice": info.get("targetLowPrice"),
    "recommendationKey": info.get("recommendationKey"),
    "totalCash": info.get("totalCash"),
    "totalDebt": info.get("totalDebt"),
    "enterpriseValue": info.get("enterpriseValue"),
    "operatingCashflow": info.get("operatingCashflow"),
    "freeCashflow": info.get("freeCashflow"),
    "revenue": info.get("totalRevenue"),
    "profitMargins": info.get("profitMargins")
}
print(json.dumps(res, indent=2))
