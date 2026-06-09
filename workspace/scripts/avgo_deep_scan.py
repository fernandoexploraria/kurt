from tradingview_ta import TA_Handler, Interval
import json

def get_ta_multi_timeframe(symbol, exchange, screener):
    intervals = {
        "1D": Interval.INTERVAL_1_DAY,
        "1W": Interval.INTERVAL_1_WEEK,
        "1M": Interval.INTERVAL_1_MONTH
    }
    
    results = {}
    
    for label, interval in intervals.items():
        handler = TA_Handler(
            symbol=symbol,
            screener=screener,
            exchange=exchange,
            interval=interval
        )
        analysis = handler.get_analysis()
        results[label] = {
            "summary": analysis.summary,
            "rsi": analysis.indicators.get("RSI"),
            "macd": analysis.indicators.get("MACD.macd"),
            "macd_signal": analysis.indicators.get("MACD.signal"),
            "adx": analysis.indicators.get("ADX"),
            "stoch_k": analysis.indicators.get("Stoch.K"),
            "stoch_d": analysis.indicators.get("Stoch.D"),
            "cci": analysis.indicators.get("CCI20"),
            "ao": analysis.indicators.get("AO"),
            "mom": analysis.indicators.get("Mom")
        }
    
    # Resistance/Support from Daily
    daily_handler = TA_Handler(
        symbol=symbol,
        screener=screener,
        exchange=exchange,
        interval=Interval.INTERVAL_1_DAY
    )
    daily_analysis = daily_handler.get_analysis()
    results["levels"] = {
        "S1": daily_analysis.indicators.get("Pivot.M.Classic.S1"),
        "S2": daily_analysis.indicators.get("Pivot.M.Classic.S2"),
        "S3": daily_analysis.indicators.get("Pivot.M.Classic.S3"),
        "R1": daily_analysis.indicators.get("Pivot.M.Classic.R1"),
        "R2": daily_analysis.indicators.get("Pivot.M.Classic.R2"),
        "R3": daily_analysis.indicators.get("Pivot.M.Classic.R3")
    }

    return results

if __name__ == '__main__':
    data = get_ta_multi_timeframe("AVGO", "NASDAQ", "america")
    print(json.dumps(data, indent=2))
