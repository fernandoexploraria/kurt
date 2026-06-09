import yfinance as yf
import pandas as pd
import numpy as np

def run_backtest(ticker="NVDA", period="3y"):
    print(f"Downloading {period} historical data for {ticker}...\n")
    df = yf.download(ticker, period=period, progress=False)
    
    # Flatten columns if multi-index (yfinance behavior for single tickers)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    # 1. Calculate the Math (ATR and Trend)
    df['Prev_Close'] = df['Close'].shift(1)
    df['TR1'] = df['High'] - df['Low']
    df['TR2'] = abs(df['High'] - df['Prev_Close'])
    df['TR3'] = abs(df['Low'] - df['Prev_Close'])
    df['TR'] = df[['TR1', 'TR2', 'TR3']].max(axis=1)
    
    # 14-Day Average True Range (ATR)
    df['ATR'] = df['TR'].rolling(window=14).mean()
    
    # 50-Day Exponential Moving Average (Trend indicator for re-entry)
    df['EMA_50'] = df['Close'].ewm(span=50, adjust=False).mean()
    
    df.dropna(inplace=True)

    # 2. Benchmark (What if we just bought and held?)
    start_price = float(df['Close'].iloc[0])
    end_price = float(df['Close'].iloc[-1])
    buy_and_hold_return = ((end_price - start_price) / start_price) * 100

    print(f"=== THE QUANT SANDBOX: {ticker} (Last {period}) ===")
    print(f"Benchmark Buy & Hold Return: {buy_and_hold_return:.2f}%\n")

    # 3. The Matrix (Testing different Trailing Multipliers)
    multipliers = [1.5, 2.0, 2.5, 3.0, 3.5, 4.0]
    
    print("Simulating Trailing Stop Architectures (Entry on Uptrend > 50 EMA):")
    print("-" * 60)
    
    for m in multipliers:
        in_position = False
        entry_price = 0.0
        highest_seen = 0.0
        total_compound_return = 1.0 
        trade_count = 0
        
        for index, row in df.iterrows():
            close = float(row['Close'])
            atr = float(row['ATR'])
            ema = float(row['EMA_50'])
            
            if not in_position:
                # Basic Entry Rule: Buy when stock establishes an uptrend
                if close > ema:
                    in_position = True
                    entry_price = close
                    highest_seen = close
                    trade_count += 1
            else:
                # Update highest seen and floor
                highest_seen = max(highest_seen, close)
                trailing_floor = highest_seen - (atr * m)
                
                # The Stop-Loss Trigger
                if close < trailing_floor:
                    in_position = False
                    trade_return = (close - entry_price) / entry_price
                    total_compound_return *= (1 + trade_return)

        # Close any open position at the end of the 3 years to finalize math
        if in_position:
            trade_return = (end_price - entry_price) / entry_price
            total_compound_return *= (1 + trade_return)

        final_pct = (total_compound_return - 1) * 100
        
        # Highlight our current hardcoded architecture
        marker = " <--- OUR CURRENT ARCHITECTURE" if m == 3.0 else ""
        print(f"ATR Multiplier: {m}x | Trades Executed: {trade_count:2} | Total Return: {final_pct:7.2f}%{marker}")

if __name__ == "__main__":
    run_backtest("NVDA", "3y")
