import numpy as np
import pandas as pd
import yfinance as yf
import datetime

# Fetch AMZN historical data
ticker_symbol = "AMZN"
stock = yf.Ticker(ticker_symbol)
df = stock.history(period="90d")

current_price = 232.05
target_price = 250.00
limit_price = 224.47
atr_14 = 7.64

# Scenario A: Limit order at 224.47
# Scenario B: Market buy at 232.05

# 1. Historical Backtest of "Limit vs Market" Entry Policy
# We analyze every day in the last 90 days. If we had placed a Limit Order at 3.43% discount from previous close,
# does it get filled in the next 5 days? If filled, does it hit target (+11% from entry) before hitting stop (2.5x ATR)?
# And if we bought at market immediately, does it hit target (+7.7% from current) before stop (2.5x ATR)?

results = []
window_days = 20  # Look-ahead window for trade resolution

for i in range(14, len(df) - window_days):
    prev_close = df['Close'].iloc[i-1]
    ref_atr = df['Close'].iloc[i-14:i].diff().abs().mean()  # Simple proxy for ATR
    
    day_open = df['Open'].iloc[i]
    day_high = df['High'].iloc[i]
    day_low = df['Low'].iloc[i]
    day_close = df['Close'].iloc[i]
    
    # Target and stop thresholds
    # Strategy A: Limit Order Trap (at 3.43% discount)
    limit_target_entry = prev_close * (1.0 - 0.0343)
    limit_stop = limit_target_entry - 2.5 * ref_atr
    limit_target = limit_target_entry + (target_price / limit_price - 1.0) * limit_target_entry # equivalent relative upside
    
    # Strategy B: Immediate Market Buy
    market_entry = day_open
    market_stop = market_entry - 2.5 * ref_atr
    market_target = market_entry + (target_price / current_price - 1.0) * market_entry
    
    # Track resolution of Strategy A (Limit)
    limit_filled = False
    limit_win = False
    limit_resolved = False
    limit_fill_index = -1
    
    # Track resolution of Strategy B (Market)
    market_resolved = False
    market_win = False
    
    # Look ahead
    for j in range(i, i + window_days):
        future_candle = df.iloc[j]
        f_open = future_candle['Open']
        f_high = future_candle['High']
        f_low = future_candle['Low']
        f_close = future_candle['Close']
        
        # 1. Resolve Strategy A (Limit Trap)
        if not limit_filled:
            if f_low <= limit_target_entry:
                limit_filled = True
                limit_fill_index = j
                # Check same bar resolution
                if f_high >= limit_target:
                    limit_win = True
                    limit_resolved = True
                elif f_low <= limit_stop:
                    limit_win = False
                    limit_resolved = True
        else:
            if not limit_resolved:
                if f_high >= limit_target:
                    limit_win = True
                    limit_resolved = True
                elif f_low <= limit_stop:
                    limit_win = False
                    limit_resolved = True
                    
        # 2. Resolve Strategy B (Market Immediate)
        if not market_resolved:
            if f_high >= market_target:
                market_win = True
                market_resolved = True
            elif f_low <= market_stop:
                market_win = False
                market_resolved = True
                
    results.append({
        "date": df.index[i].strftime("%Y-%m-%d"),
        "prev_close": prev_close,
        "limit_entry_target": limit_target_entry,
        "limit_filled": limit_filled,
        "limit_win": limit_win if limit_resolved else None,
        "market_win": market_win if market_resolved else None
    })

results_df = pd.DataFrame(results)
limit_fill_rate = results_df['limit_filled'].mean()
limit_success_rate = results_df[results_df['limit_filled'] & results_df['limit_win'].notnull()]['limit_win'].mean()
market_success_rate = results_df['market_win'].mean()

# 2. Forward-Looking Monte Carlo Projection
# Based on current price of 232.05, daily ATR of 7.64, daily volatility proxy of 2.2%
daily_vol = (atr_14 / current_price) / 1.5 # daily standard deviation proxy
sim_runs = 50000
sim_days = 45 # 45 day look-ahead corresponding to Schwab Options contract window

limit_fills = 0
limit_wins = 0
limit_losses = 0
limit_pending = 0

market_wins = 0
market_losses = 0
market_pending = 0

# Market Buy Parameters
m_entry = current_price
m_target = target_price
m_stop = m_entry - 2.5 * atr_14 # 212.95

# Limit Trap Parameters
l_trigger = limit_price
l_target = target_price
l_stop = l_trigger - 2.5 * atr_14 # 205.37

np.random.seed(42)

for run in range(sim_runs):
    # Simulate a GBM price path
    drift = 0.0003 # minor positive drift
    shocks = np.random.normal(0, daily_vol, sim_days)
    price_path = [current_price]
    for shock in shocks:
        price_path.append(price_path[-1] * np.exp(drift + shock))
        
    price_path = np.array(price_path)
    
    # Resolve Market Buy immediately
    m_win_day = np.where(price_path >= m_target)[0]
    m_loss_day = np.where(price_path <= m_stop)[0]
    
    if len(m_win_day) > 0 and len(m_loss_day) > 0:
        if m_win_day[0] < m_loss_day[0]:
            market_wins += 1
        else:
            market_losses += 1
    elif len(m_win_day) > 0:
        market_wins += 1
    elif len(m_loss_day) > 0:
        market_losses += 1
    else:
        market_pending += 1
        
    # Resolve Limit Trap
    l_fill_day = np.where(price_path <= l_trigger)[0]
    if len(l_fill_day) > 0:
        limit_fills += 1
        fill_idx = l_fill_day[0]
        # Simulate path post-fill
        post_fill_path = price_path[fill_idx:]
        l_win_day = np.where(post_fill_path >= l_target)[0]
        l_loss_day = np.where(post_fill_path <= l_stop)[0]
        
        if len(l_win_day) > 0 and len(l_loss_day) > 0:
            if l_win_day[0] < l_loss_day[0]:
                limit_wins += 1
            else:
                limit_losses += 1
        elif len(l_win_day) > 0:
            limit_wins += 1
        elif len(l_loss_day) > 0:
            limit_losses += 1
        else:
            limit_pending += 1

print(f"=== HISTORICAL BACKTEST RESULTS (Last 90 Days) ===")
print(f"Limit Trap Target: ${limit_price:.2f} | Market Buy Target: ${current_price:.2f}")
print(f"Limit Trap Fill Rate: {limit_fill_rate:.2%}")
print(f"Limit Trap Win Rate (when filled): {limit_success_rate:.2%}")
print(f"Immediate Market Buy Win Rate: {market_success_rate:.2%}")
print()
print(f"=== FORWARD-LOOKING MONTE CARLO PROJECTION (45-Day Horizon) ===")
print(f"Simulation Runs: {sim_runs:,}")
print(f"Daily Volatility Proxy: {daily_vol:.2%} (ATR-derived)")
print()
print(f"Strategy A: Limit Trap at ${limit_price:.2f} (Stop: ${l_stop:.2f} | Target: ${l_target:.2f})")
print(f"  - Probability of getting FILLED: {limit_fills / sim_runs:.2%}")
print(f"  - Probability of getting FILLED & WINNING: {limit_wins / sim_runs:.2%}")
print(f"  - Probability of getting FILLED & LOSING: {limit_losses / sim_runs:.2%}")
print(f"  - Probability of remaining UNFILLED: {(sim_runs - limit_fills) / sim_runs:.2%}")
print(f"  - Win Rate when filled: {limit_wins / max(1, limit_fills):.2%}")
print()
print(f"Strategy B: Immediate Market Buy at ${current_price:.2f} (Stop: ${m_stop:.2f} | Target: ${m_target:.2f})")
print(f"  - Probability of WINNING (hits ${target_price:.2f} first): {market_wins / sim_runs:.2%}")
print(f"  - Probability of LOSING (hits ${m_stop:.2f} first): {market_losses / sim_runs:.2%}")
print(f"  - Probability of remaining active: {market_pending / sim_runs:.2%}")
print()
print(f"=== EXPECTED P&L COMPARISON (Outlay Sized to 8 Shares = $1,795.76) ===")
# Outlays
outlay_market = current_price * 8 # $1856.40
outlay_limit = limit_price * 8 # $1795.76
# Trade Profits/Losses
profit_market = (m_target - m_entry) * 8 # (250 - 232.05) * 8 = $143.60
loss_market = (m_stop - m_entry) * 8 # -2.5 * 7.64 * 8 = -$152.80
exp_pnl_market = (market_wins / sim_runs) * profit_market + (market_losses / sim_runs) * loss_market

profit_limit = (l_target - l_trigger) * 8 # (250 - 224.47) * 8 = $204.24
loss_limit = (l_stop - l_trigger) * 8 # -2.5 * 7.64 * 8 = -$152.80
# If unfilled, return is 0 (or risk-free rate of 4.5% on capital held in SGOV during the 45 days)
sgov_rate_45d = 0.045 * (45.0/365.0)
unfilled_return = outlay_limit * sgov_rate_45d # yield on cash
exp_pnl_limit = (limit_wins / sim_runs) * profit_limit + (limit_losses / sim_runs) * loss_limit + ((sim_runs - limit_fills) / sim_runs) * unfilled_return

print(f"Strategy A (Limit Trap) Expected P&L: ${exp_pnl_limit:+.2f} (includes 4.5% yield on unfilled cash)")
print(f"Strategy B (Market Buy) Expected P&L: ${exp_pnl_market:+.2f}")
