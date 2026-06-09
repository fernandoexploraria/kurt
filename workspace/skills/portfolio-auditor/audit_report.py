import sys
import json
import yfinance as yf

def run_audit(trades_json):
    try:
        trades = json.loads(trades_json)
    except Exception as e:
        return f"Error parsing trades: {e}. Please ensure input is valid JSON."

    if not trades:
        return "No trades provided to audit."

    # Fetch SPY benchmark to compare against
    spy = yf.Ticker("SPY")
    
    total_trades = len(trades)
    winning_trades = 0
    
    report = ["## 📊 Portfolio Audit & Alpha Report\n"]
    
    for trade in trades:
        ticker = trade.get('ticker')
        entry_price = float(trade.get('entry_price', 0))
        entry_date = trade.get('date') # Expected format: YYYY-MM-DD
        
        try:
            # 1. Get current stock price
            stock = yf.Ticker(ticker)
            current_price = stock.history(period="1d")['Close'].iloc[-1]
            
            # 2. Calculate Trade Return (Thesis Accuracy)
            trade_return = ((current_price - entry_price) / entry_price) * 100
            
            if trade_return > 0:
                winning_trades += 1
            
            # 3. Get SPY return since the entry date
            spy_history = spy.history(start=entry_date)
            if not spy_history.empty:
                spy_entry = spy_history['Close'].iloc[0]
                spy_current = spy_history['Close'].iloc[-1]
                spy_return = ((spy_current - spy_entry) / spy_entry) * 100
            else:
                spy_return = 0.0
            
            # 4. Calculate Decision Alpha (Our Return - Market Return)
            alpha = trade_return - spy_return
            
            # 5. Format the output for this ticker
            report.append(f"### 📈 {ticker} (Entered {entry_date} @ ${entry_price:.2f})")
            report.append(f"- **Current Price:** ${current_price:.2f}")
            report.append(f"- **Thesis Accuracy (Total Return):** {trade_return:.2f}%")
            report.append(f"- **SPY Benchmark Return:** {spy_return:.2f}%")
            report.append(f"- **Decision Alpha:** {alpha:.2f}%\n")
            
        except Exception as e:
            report.append(f"### ⚠️ {ticker} - Error calculating data: {e}\n")

    # Calculate overall metrics
    win_rate = (winning_trades / total_trades) * 100 if total_trades > 0 else 0
    report.insert(1, f"**Overall Win Rate:** {win_rate:.2f}% ({winning_trades}/{total_trades} trades are profitable)")
    report.insert(2, "---\n")
    
    return "\n".join(report)

if __name__ == "__main__":
    if len(sys.argv) > 1:
        print(run_audit(sys.argv[1]))
    else:
        print("Error: Please provide a JSON string of trades as an argument.")
