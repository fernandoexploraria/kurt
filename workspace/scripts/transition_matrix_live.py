#!/usr/bin/env python3
"""
Transition-Matrix Analysis for Stock-Trading Dynamics
Live Market Estimator (MVE Phase 1 & 2)

This script pulls real-world 1-minute bar data for any watchlisted ticker, 
discretizes log returns into K states using a chronological training split 
(preventing look-ahead bias), estimates smoothed baseline vs. live transition 
matrices, and measures structural regime changes via Jensen-Shannon Divergence (JSD).

Usage:
  python3 transition_matrix_live.py --ticker MCD --states 8

Author: Kurt Richardson (AI Portfolio Manager)
Date: July 17, 2026
"""

import argparse
import numpy as np
import pandas as pd
import yfinance as yf
import json
import sys

class LiveTransitionMatrixEstimator:
    def __init__(self, ticker, k_states=8, alpha=0.5):
        self.ticker = ticker.upper()
        self.k_states = k_states
        self.alpha = alpha
        self.boundaries = None
        
    def fetch_data(self):
        """
        Fetches the last 5 days of 1-minute bar data from yfinance.
        """
        print(f"[+] Fetching 1-minute bar data for {self.ticker} (last 5 days)...")
        stock = yf.Ticker(self.ticker)
        df = stock.history(period="5d", interval="1m")
        if df.empty:
            raise ValueError(f"No data returned for ticker {self.ticker}. Check if symbol is valid.")
        
        # Calculate log returns
        df['Log_Return'] = np.log(df['Close'] / df['Close'].shift(1))
        df = df.dropna(subset=['Log_Return'])
        print(f"[+] Successfully loaded {len(df)} 1-minute intervals.")
        return df

    def split_data(self, df):
        """
        Splits data chronologically: First 4 days for Baseline (P_0), Last 1 day for Live (P).
        This guarantees point-in-time discipline.
        """
        unique_dates = df.index.normalize().unique()
        if len(unique_dates) < 2:
            # Fallback if we have less than 2 distinct days
            split_idx = int(len(df) * 0.8)
            baseline_df = df.iloc[:split_idx]
            live_df = df.iloc[split_idx:]
        else:
            baseline_dates = unique_dates[:-1]
            live_date = unique_dates[-1]
            baseline_df = df[df.index.normalize().isin(baseline_dates)]
            live_df = df[df.index.normalize() == live_date]
            
        print(f"  - Baseline Window (P0): {baseline_df.index[0]} to {baseline_df.index[-1]} ({len(baseline_df)} bars)")
        print(f"  - Live Window (P):      {live_df.index[0]} to {live_df.index[-1]} ({len(live_df)} bars)")
        return baseline_df, live_df

    def fit_boundaries(self, baseline_returns):
        """
        Fits quantization boundaries using baseline returns only.
        """
        quantiles = np.linspace(0, 1, self.k_states + 1)
        self.boundaries = np.percentile(baseline_returns, quantiles * 100)
        self.boundaries[0] = -np.inf
        self.boundaries[-1] = np.inf
        return self.boundaries

    def discretize(self, returns):
        if self.boundaries is None:
            raise ValueError("Boundaries must be fitted first!")
        states = np.digitize(returns, self.boundaries)
        return np.clip(states, 1, self.k_states)

    def estimate_matrix(self, states):
        K = self.k_states
        counts = np.zeros((K, K))
        for t in range(len(states) - 1):
            i = states[t] - 1
            j = states[t+1] - 1
            counts[i, j] += 1
            
        matrix = np.zeros((K, K))
        for i in range(K):
            row_sum = np.sum(counts[i, :])
            matrix[i, :] = (counts[i, :] + self.alpha) / (row_sum + K * self.alpha)
            
        occupancy = np.bincount(states, minlength=K + 1)[1:]
        occupancy = occupancy / np.sum(occupancy) if np.sum(occupancy) > 0 else np.ones(K)/K
        
        return counts, matrix, occupancy

    def compute_entropy_rate(self, matrix, occupancy):
        entropy = 0.0
        for i in range(self.k_states):
            row_entropy = 0.0
            for j in range(self.k_states):
                p = matrix[i, j]
                if p > 0:
                    row_entropy -= p * np.log2(p)
            entropy += occupancy[i] * row_entropy
        return entropy

    def jensen_shannon_divergence(self, P, Q, occupancy):
        K = self.k_states
        jsd_total = 0.0
        for i in range(K):
            p_row = P[i, :]
            q_row = Q[i, :]
            m_row = 0.5 * (p_row + q_row)
            
            kl_p_m = np.sum([p * np.log2(p/m) for p, m in zip(p_row, m_row) if p > 0 and m > 0])
            kl_q_m = np.sum([q * np.log2(q/m) for q, m in zip(q_row, m_row) if q > 0 and m > 0])
            
            jsd_row = 0.5 * kl_p_m + 0.5 * kl_q_m
            jsd_total += occupancy[i] * jsd_row
        return jsd_total

def main():
    parser = argparse.ArgumentParser(description="Estimate Live Transition Matrices on Stock Returns.")
    parser.add_argument("--ticker", required=True, help="Stock ticker symbol (e.g. MCD)")
    parser.add_argument("--states", type=int, default=8, help="Number of discretization states (default: 8)")
    parser.add_argument("--alpha", type=float, default=0.5, help="Dirichlet smoothing prior (default: 0.5)")
    args = parser.parse_args()

    print("=" * 65)
    print(f"📊 LIVE TRANSITION-MATRIX DIAGNOSTIC: {args.ticker.upper()}")
    print("=" * 65)

    estimator = LiveTransitionMatrixEstimator(args.ticker, k_states=args.states, alpha=args.alpha)
    
    try:
        df = estimator.fetch_data()
        baseline_df, live_df = estimator.split_data(df)
        
        # Fit boundaries on baseline
        estimator.fit_boundaries(baseline_df['Log_Return'].values)
        
        # Discretize states
        states_base = estimator.discretize(baseline_df['Log_Return'].values)
        states_live = estimator.discretize(live_df['Log_Return'].values)
        
        # Estimate matrices
        _, P0, occ_base = estimator.estimate_matrix(states_base)
        _, P, occ_live = estimator.estimate_matrix(states_live)
        
        # Compute metrics
        h_base = estimator.compute_entropy_rate(P0, occ_base)
        h_live = estimator.compute_entropy_rate(P, occ_live)
        jsd = estimator.jensen_shannon_divergence(P, P0, occ_live)
        
        print("\n" + "=" * 55)
        print("📈 QUANTITATIVE ESTIMATES SUMMARY")
        print("=" * 55)
        print(f"  Baseline Entropy Rate:       {h_base:.4f} bits/transition")
        print(f"  Live Window Entropy Rate:    {h_live:.4f} bits/transition")
        print(f"  Regime Divergence (JSD):     {jsd:.4f} (Bounded [0, 1])")
        print("=" * 55)
        
        # Diagonal analysis (self-persistence)
        diag_base = np.diag(P0)
        diag_live = np.diag(P)
        
        print("\n🔍 State Self-Persistence (Matrix Diagonal Shift):")
        print("  State ID:      " + "   ".join([f"S{i+1}" for i in range(args.states)]))
        print("  Baseline (P0): " + " ".join([f"{val:.2f}" for val in diag_base]))
        print("  Live (P):      " + " ".join([f"{val:.2f}" for val in diag_live]))
        
        # Interpret JSD
        print("\n💡 Strategic Diagnosis:")
        if jsd > 0.15:
            print(f"  ⚠️ HIGH REGIME DIVERGENCE DETECTED (JSD = {jsd:.4f})!")
            print(f"  The micro-structural return grammar for {args.ticker.upper()} today differs significantly")
            print("  from the prior 4 days. This indicates an active trend expansion, volatility break,")
            print("  or institutional positioning regime.")
        else:
            print(f"  ✅ STABLE REGIME CONFIRMED (JSD = {jsd:.4f}).")
            print(f"  {args.ticker.upper()} is trading within nominal statistical baseline parameters.")
            print("  No major order-book or structural return mutations are occurring.")
        print("=" * 65)
        
    except Exception as e:
        print(f"\n❌ Error during execution: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
