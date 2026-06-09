import sys
import json
import yfinance as yf

def run_strategy_assessment(input_json):
    try:
        data = json.loads(input_json)
    except Exception as e:
        return f"Error parsing input data: {e}. Please ensure input is valid JSON."

    portfolio_stats = data.get('portfolio_stats', {})
    positions = data.get('positions', [])

    cash_pct = float(portfolio_stats.get('cash_percentage', 100.0))
    weekly_drawdown = float(portfolio_stats.get('weekly_drawdown_pct', 0.0))
    overall_growth = float(portfolio_stats.get('overall_growth_pct', 0.0))

    report = ["## 🛡️ Performance Strategist: Portfolio Directives\n"]
    alerts = []
    
    # Global State Evaluations
    if overall_growth >= 10.0:
        alerts.append(f"🚨 **GLOBAL ANTI-GREED TRIGGERED:** Cumulative portfolio growth is sitting at **{overall_growth:.2f}%** (Threshold: >=10%). Evaluating positions for strategic profit trimming.")
    
    if weekly_drawdown <= -5.0:
        alerts.append(f"⚠️ **GLOBAL BEAR SHIELD PANIC TRIGGERED:** Weekly drawdown has crossed **{weekly_drawdown:.2f}%** (Threshold: <=-5%). Prioritizing preservation of capital.")

    if cash_pct < 15.0:
        report.append(f"⚠️ **CASH BUFFER CRITICAL:** Cash reserves are at **{cash_pct:.2f}%** (Target: 15.00%). A capital rebalance is required.\n")

    if alerts:
        report.append("### ⚡ System Alerts")
        for alert in alerts:
            report.append(f"- {alert}")
        report.append("\n---\n")

    position_directives = []
    highest_return = -9999.0
    best_ticker = None
    lowest_return = 9999.0
    worst_ticker = None

    report.append("### 📋 Position Breakdown & Rules Assessment")

    for pos in positions:
        ticker = pos.get('ticker')
        entry_price = float(pos.get('entry_price', 0))
        
        try:
            stock = yf.Ticker(ticker)
            current_price = stock.history(period="1d")['Close'].iloc[-1]
            trade_return = ((current_price - entry_price) / entry_price) * 100
            
            # Track extremes for potential cash rebalancing rules
            if trade_return > highest_return:
                highest_return = trade_return
                best_ticker = ticker
            if trade_return < lowest_return:
                lowest_return = trade_return
                worst_ticker = ticker

            directive = "🟢 HOLD"
            reason = "Position is within normal parameters."

            # Rule 1: Bear Shield Stop-Loss (-8%)
            if trade_return <= -8.0:
                directive = "🔴 PROTECT / STOP-LOSS"
                reason = f"Position loss has reached **{trade_return:.2f}%**, violating the -8% risk threshold."
            
            # Rule 2: Anti-Greed Take-Profit (+20% individual or +10% portfolio)
            elif trade_return >= 20.0:
                directive = "🟡 TRIM (Take Profit)"
                reason = f"Exceptional individual performance at **{trade_return:.2f}%**. Sell 1/3 of the position to lock in gains."
            elif overall_growth >= 10.0:
                directive = "🟡 TRIM (Global Anti-Greed)"
                reason = f"Portfolio milestone met (+{overall_growth:.2f}%). Trim overextended gains to secure the balance."
            
            # Rule 2b: Global Bear Shield Risk Mitigation
            elif weekly_drawdown <= -5.0:
                directive = "🟠 DE-RISK"
                reason = "Global weekly drawdown protection active. Tighten stops or convert weak assets to cash."

            report.append(f"#### **{ticker}** | Directive: `{directive}`")
            report.append(f"- **Entry Price:** ${entry_price:.2f} | **Current Price:** ${current_price:.2f}")
            report.append(f"- **Total Return:** {trade_return:.2f}%")
            report.append(f"- **Action Plan:** {reason}\n")

        except Exception as e:
            report.append(f"#### ⚠️ **{ticker}** - Error analyzing position: {e}\n")

    # Rule 3: Actionable Cash Rebalancing Recommendations
    if cash_pct < 15.0 and positions:
        report.append("---\n### 💵 Cash Rebalancing Directives")
        report.append(f"To raise your cash buffer from **{cash_pct:.2f}%** up to the target **15.00%**, execute one of these systemic recommendations:")
        if best_ticker and highest_return > 0:
            report.append(f"- 📈 **Option A (Trim Winner):** Trim your most overextended position (**{best_ticker}** sitting at **+{highest_return:.2f}%**) to secure realized capital.")
        if worst_ticker:
            report.append(f"- 📉 **Option B (Cut Tail):** Liquidate or scale down your underperforming leg (**{worst_ticker}** at **{lowest_return:.2f}%**) to cut trailing deadweight.")

    return "\n".join(report)

if __name__ == "__main__":
    if len(sys.argv) > 1:
        print(run_strategy_assessment(sys.argv[1]))
    else:
        print("Error: Please provide a valid portfolio JSON data string.")
