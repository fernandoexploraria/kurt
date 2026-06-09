from tradingview_ta import TA_Handler, Interval

def get_avgo_ta():
    avgo = TA_Handler(
        symbol="AVGO",
        screener="america",
        exchange="NASDAQ",
        interval=Interval.INTERVAL_1_DAY
    )
    analysis = avgo.get_analysis()
    
    print(f"Summary: {analysis.summary}")
    print(f"Oscillators: {analysis.oscillators}")
    print(f"Moving Averages: {analysis.moving_averages}")
    print(f"Indicators: {analysis.indicators['RSI']}")
    print(f"MACD: {analysis.indicators['MACD.macd']}, Signal: {analysis.indicators['MACD.signal']}")
    print(f"ADX: {analysis.indicators['ADX']}")

if __name__ == '__main__':
    get_avgo_ta()
