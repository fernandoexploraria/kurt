import argparse
import numpy as np
import pandas as pd
import yfinance as yf
import sys

def main():
    parser = argparse.ArgumentParser(description="Generic Dual-Strategy Backtest and Monte Carlo Simulator")
    parser.add_argument("--ticker", default="AAPL", help="Stock ticker symbol (default: AAPL)")
    parser.add_argument("--current", type=float, help="Current market price (default: fetch live)")
    parser.add_argument("--target", type=float, help="Target price (default: 1.10 * current)")
    parser.add_argument("--limit", type=float, help="Limit buy price (default: 0.95 * current)")
    parser.add_argument("--shares", type=int, default=8, help="Number of shares to size the outlay (default: 8)")
    args = parser.parse_args()

    ticker_symbol = args.ticker.upper()
    print(f"Fetching historical data for {ticker_symbol} from yfinance...")
    stock = yf.Ticker(ticker_symbol)
    df = stock.history(period="90d")

    if df.empty:
        print(f"Error: Could not retrieve data for ticker {ticker_symbol}")
        sys.exit(1)

    # 1. Resolve inputs
    live_last = df['Close'].iloc[-1]
    current_price = args.current if args.current is not None else live_last
    
    # Calculate 14-day ATR dynamically
    # True Range = max(H-L, |H-Cp|, |L-Cp|)
    high_low = df['High'] - df['Low']
    high_close_prev = (df['High'] - df['Close'].shift(1)).abs()
    low_close_prev = (df['Low'] - df['Close'].shift(1)).abs()
    tr = pd.concat([high_low, high_close_prev, low_close_prev], axis=1).max(axis=1)
    atr_14 = tr.iloc[-14:].mean()

    # Use defaults if not specified
    # For AAPL, if no custom parameters, try to align with the Watchlist targets
    if ticker_symbol == "AAPL":
        limit_price = args.limit if args.limit is not None else 278.50
        target_price = args.target if args.target is not None else 345.00
    else:
        limit_price = args.limit if args.limit is not None else (current_price * 0.95)
        target_price = args.target if args.target is not None else (current_price * 1.10)

    print(f"\nConfiguration for {ticker_symbol}:")
    print(f"  - Current Market Price: ${current_price:.2f}")
    print(f"  - Target Price: ${target_price:.2f} (+{((target_price/current_price)-1)*100:.2f}%)")
    print(f"  - Limit Buy Trap: ${limit_price:.2f} (-{((1-limit_price/current_price))*100:.2f}%)")
    print(f"  - Dynamic ATR (14D): ${atr_14:.2f}")
    print(f"  - Outlay Sizing: {args.shares} shares")

    # 2. Historical Backtest of "Limit vs Market" Entry Policy
    results = []
    window_days = 20  # Look-ahead window for trade resolution

    for i in range(14, len(df) - window_days):
        prev_close = df['Close'].iloc[i-1]
        # Local ATR for that historical point
        sub_tr = tr.iloc[i-14:i]
        ref_atr = sub_tr.mean()
        
        day_open = df['Open'].iloc[i]
        day_high = df['High'].iloc[i]
        day_low = df['Low'].iloc[i]
        day_close = df['Close'].iloc[i]
        
        # Strategy A: Limit Order Trap
        # Calculate equivalent relative discount or hard discount
        rel_limit_discount = limit_price / current_price
        limit_target_entry = prev_close * rel_limit_discount
        limit_stop = limit_target_entry - 2.5 * ref_atr
        # Equivalent upside target
        rel_target_upside = target_price / limit_price
        limit_target = limit_target_entry * rel_target_upside
        
        # Strategy B: Immediate Market Buy
        market_entry = day_open
        market_stop = market_entry - 2.5 * ref_atr
        rel_market_upside = target_price / current_price
        market_target = market_entry * rel_market_upside
        
        # Track resolution of Strategy A (Limit)
        limit_filled = False
        limit_win = False
        limit_resolved = False
        
        # Track resolution of Strategy B (Market)
        market_resolved = False
        market_win = False
        
        # Look ahead window
        for j in range(i, i + window_days):
            future_candle = df.iloc[j]
            f_high = future_candle['High']
            f_low = future_candle['Low']
            
            # Resolve Strategy A (Limit Trap)
            if not limit_filled:
                if f_low <= limit_target_entry:
                    limit_filled = True
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
                        
            # Resolve Strategy B (Market Immediate)
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

    # 3. Forward-Looking Monte Carlo Projection
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
    m_stop = m_entry - 2.5 * atr_14

    # Limit Trap Parameters
    l_trigger = limit_price
    l_target = target_price
    l_stop = l_trigger - 2.5 * atr_14

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

    print("\n" + "="*50)
    print(f"📈 REPLAY SIMULATION RESULTS: {ticker_symbol}")
    print("="*50)
    print(f"=== HISTORICAL BACKTEST RESULTS (Last 90 Days) ===")
    print(f"Limit Trap Target: ${limit_price:.2f} | Market Buy Target: ${current_price:.2f}")
    print(f"Limit Trap Fill Rate: {limit_fill_rate:.2%}")
    print(f"Limit Trap Win Rate (when filled): {limit_success_rate:.2%}" if limit_success_rate is not None and not np.isnan(limit_success_rate) else "Limit Trap Win Rate (when filled): N/A (no fills in backtest)")
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
    print(f"=== EXPECTED P&L COMPARISON (Outlay Sized to {args.shares} Shares = ${current_price * args.shares:.2f}) ===")
    
    outlay_market = current_price * args.shares
    outlay_limit = limit_price * args.shares
    
    profit_market = (m_target - m_entry) * args.shares
    loss_market = (m_stop - m_entry) * args.shares
    exp_pnl_market = (market_wins / sim_runs) * profit_market + (market_losses / sim_runs) * loss_market

    profit_limit = (l_target - l_trigger) * args.shares
    loss_limit = (l_stop - l_trigger) * args.shares
    sgov_rate_45d = 0.045 * (45.0/365.0)
    unfilled_return = outlay_limit * sgov_rate_45d
    exp_pnl_limit = (limit_wins / sim_runs) * profit_limit + (limit_losses / sim_runs) * loss_limit + ((sim_runs - limit_fills) / sim_runs) * unfilled_return

    print(f"Strategy A (Limit Trap) Expected P&L: ${exp_pnl_limit:+.2f} (includes 4.5% yield on unfilled cash)")
    print(f"Strategy B (Market Buy) Expected P&L: ${exp_pnl_market:+.2f}")
    print("="*50)

if __name__ == "__main__":
    main()
