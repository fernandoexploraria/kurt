import json
import sys
from tradingview_ta import TA_Handler, Interval

def get_ta_score(ticker, exchange="NASDAQ"):
    try:
        handler = TA_Handler(
            symbol=ticker,
            exchange=exchange,
            screener="america",
            interval=Interval.INTERVAL_1_DAY
        )
        analysis = handler.get_analysis()
        return {
            "summary": analysis.summary,
            "indicators": analysis.indicators
        }
    except Exception as e:
        if exchange == "NASDAQ":
            return get_ta_score(ticker, "NYSE")
        return {"error": str(e)}

ticker = sys.argv[1] if len(sys.argv) > 1 else "GNRC"
result = get_ta_score(ticker)
print(json.dumps(result, indent=2))
