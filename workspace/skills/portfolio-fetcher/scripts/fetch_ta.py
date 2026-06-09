import yfinance as yf
import pandas as pd
import numpy as np
import sys

def calculate_rsi(series, periods=14):
    close_delta = series.diff()
    up = close_delta.clip(lower=0)
    down = -1 * close_delta.clip(upper=0)
    ma_up = up.ewm(com=periods - 1, adjust=True, min_periods=periods).mean()
    ma_down = down.ewm(com=periods - 1, adjust=True, min_periods=periods).mean()
    rsi = ma_up / ma_down
    rsi = 100 - (100 / (1 + rsi))
    return rsi.iloc[-1]

def calculate_macd(series, short=12, long=26, signal=9):
    exp1 = series.ewm(span=short, adjust=False).mean()
    exp2 = series.ewm(span=long, adjust=False).mean()
    macd = exp1 - exp2
    sig = macd.ewm(span=signal, adjust=False).mean()
    return macd.iloc[-1], sig.iloc[-1]

args = sys.argv[1:]
ticker = args[0] if args else 'AFL'
t = yf.Ticker(ticker)
data = t.history(period='6mo')
close_series = data['Close']

rsi = calculate_rsi(close_series)
macd, sig = calculate_macd(close_series)

print(f"Ticker: {ticker}")
print(f"RSI (14): {rsi:.2f}")
print(f"MACD: {macd:.2f}, Signal: {sig:.2f}")

