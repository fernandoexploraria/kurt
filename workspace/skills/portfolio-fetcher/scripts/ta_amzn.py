import yfinance as yf
import pandas as pd
import numpy as np

def calculate_rsi(data, window=14):
    delta = data['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=window).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=window).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def calculate_macd(data, slow=26, fast=12, signal=9):
    ema_fast = data['Close'].ewm(span=fast, adjust=False).mean()
    ema_slow = data['Close'].ewm(span=slow, adjust=False).mean()
    macd = ema_fast - ema_slow
    signal_line = macd.ewm(span=signal, adjust=False).mean()
    return macd, signal_line

def calculate_adx(data, window=14):
    plus_dm = data['High'].diff()
    minus_dm = data['Low'].diff()
    plus_dm[plus_dm < 0] = 0
    minus_dm[minus_dm > 0] = 0
    
    tr1 = pd.DataFrame(data['High'] - data['Low'])
    tr2 = pd.DataFrame(abs(data['High'] - data['Close'].shift(1)))
    tr3 = pd.DataFrame(abs(data['Low'] - data['Close'].shift(1)))
    frames = [tr1, tr2, tr3]
    tr = pd.concat(frames, axis=1, join='inner').max(axis=1)
    atr = tr.rolling(window).mean()
    
    plus_di = 100 * (plus_dm.ewm(alpha=1/window).mean() / atr)
    minus_di = abs(100 * (minus_dm.ewm(alpha=1/window).mean() / atr))
    dx = (abs(plus_di - minus_di) / abs(plus_di + minus_di)) * 100
    adx = ((dx.shift(1) * (window - 1)) + dx) / window
    adx_smooth = dx.ewm(alpha=1/window).mean()
    return adx_smooth

ticker = "AMZN"
df = yf.download(ticker, period='6mo')

rsi = calculate_rsi(df).iloc[-1]
macd, signal = calculate_macd(df)
macd_val = macd.iloc[-1]
sig_val = signal.iloc[-1]
adx_val = calculate_adx(df).iloc[-1]

print(f"RSI: {rsi.values[0]:.2f}")
print(f"MACD: {macd_val.values[0]:.2f}")
print(f"Signal: {sig_val.values[0]:.2f}")
print(f"ADX: {adx_val.values[0]:.2f}")
print(f"52-Week High: {df['High'].max().values[0]:.2f}")
print(f"52-Week Low: {df['Low'].min().values[0]:.2f}")
