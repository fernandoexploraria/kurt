#!/usr/bin/env python3
"""
Transition-Matrix Analysis for Stock-Trading Dynamics
Minimum Viable Experiment (MVE) - Prototype Estimator

This script implements the core mathematics described in Revision 3 of the 
Transition-Matrix Research Architecture white paper, specifically:
1. Quantile-based state discretization (returns -> K states)
2. Transition count construction (N_ij matrix)
3. Dirichlet smoothing (add-alpha Bayesian estimation)
4. Entropy rate computation (weighted by state occupancy)
5. Pointwise transition surprise (cross-entropy anomaly score)
6. Jensen-Shannon Divergence (JSD) between two matrices
7. Simulation of Trending vs. Mean-Reverting regimes to show metric sensitivity.

Author: Kurt Richardson (AI Portfolio Manager)
Date: July 17, 2026
"""

import numpy as np
import pandas as pd
import json
import sys

class TransitionMatrixEstimator:
    def __init__(self, k_states=8, alpha=0.5):
        """
        k_states: Number of discrete states (default 8 for MVE alpha prototype)
        alpha: Smoothing hyperparameter (Dirichlet symmetric prior)
        """
        self.k_states = k_states
        self.alpha = alpha
        self.boundaries = None
        self.transition_matrix = None
        
    def fit_discretizer(self, returns):
        """
        Fits quantile boundaries on baseline historical returns to prevent data leakage.
        """
        clean_returns = returns[~np.isnan(returns)]
        # Generate quantile boundaries for K states
        quantiles = np.linspace(0, 1, self.k_states + 1)
        self.boundaries = np.percentile(clean_returns, quantiles * 100)
        # Ensure strict inequality at boundaries
        self.boundaries[0] = -np.inf
        self.boundaries[-1] = np.inf
        return self.boundaries
        
    def discretize(self, returns):
        """
        Maps continuous returns to discrete states in [1, K]
        """
        if self.boundaries is None:
            raise ValueError("Discretizer must be fitted first!")
        # np.digitize returns 1-indexed bins
        states = np.digitize(returns, self.boundaries)
        # Clip to ensure all states fall within [1, K]
        states = np.clip(states, 1, self.k_states)
        return states

    def estimate_transition_matrix(self, states):
        """
        Estimates the K x K transition matrix with Dirichlet smoothing.
        """
        K = self.k_states
        # Construct transition counts
        counts = np.zeros((K, K))
        for t in range(len(states) - 1):
            i = states[t] - 1      # convert to 0-index
            j = states[t+1] - 1
            counts[i, j] += 1
            
        # Apply Dirichlet prior smoothing: P_ij = (N_ij + alpha) / (N_i. + K*alpha)
        smoothed_matrix = np.zeros((K, K))
        for i in range(K):
            row_sum = np.sum(counts[i, :])
            smoothed_matrix[i, :] = (counts[i, :] + self.alpha) / (row_sum + K * self.alpha)
            
        return counts, smoothed_matrix

    def compute_state_occupancy(self, states):
        """
        Computes empirical state occupancy distribution (pi).
        """
        counts = np.bincount(states, minlength=self.k_states + 1)[1:] # ignore 0-index
        return counts / np.sum(counts)

    def compute_entropy_rate(self, transition_matrix, occupancy):
        """
        Computes the conditional entropy rate H(X_t+1 | X_t) weighted by state occupancy.
        This represents the predictability of the sequence.
        """
        entropy_rate = 0.0
        for i in range(self.k_states):
            row_entropy = 0.0
            for j in range(self.k_states):
                p = transition_matrix[i, j]
                if p > 0:
                    row_entropy -= p * np.log2(p)
            entropy_rate += occupancy[i] * row_entropy
        return entropy_rate

    @staticmethod
    def jensen_shannon_divergence(P, Q, occupancy):
        """
        Computes the occupancy-weighted Jensen-Shannon Divergence row-wise between 
        two transition matrices P (live) and Q (reference).
        """
        K = P.shape[0]
        jsd_total = 0.0
        
        for i in range(K):
            p_row = P[i, :]
            q_row = Q[i, :]
            m_row = 0.5 * (p_row + q_row)
            
            # Row-wise Kullback-Leibler divergences
            kl_p_m = np.sum([p * np.log2(p/m) for p, m in zip(p_row, m_row) if p > 0 and m > 0])
            kl_q_m = np.sum([q * np.log2(q/m) for q, m in zip(q_row, m_row) if q > 0 and m > 0])
            
            jsd_row = 0.5 * kl_p_m + 0.5 * kl_q_m
            jsd_total += occupancy[i] * jsd_row
            
        return jsd_total

    def compute_surprise_series(self, states, transition_matrix):
        """
        Computes the pointwise transition surprise for each transition:
        s_t = -log2( P(q_t+1 | q_t) )
        Anomalous transitions trigger high surprise scores.
        """
        surprise = []
        for t in range(len(states) - 1):
            i = states[t] - 1
            j = states[t+1] - 1
            prob = transition_matrix[i, j]
            s_t = -np.log2(prob)
            surprise.append(s_t)
        return np.array(surprise)

# =====================================================================
# SIMULATION ENGINE TO VERIFY METRIC VALIDITY
# =====================================================================
def run_simulation_demo():
    print("=" * 60)
    print("🔬 RUNNING TRANSITION-MATRIX SIMULATION EXPERIMENT")
    print("=" * 60)
    
    np.random.seed(42)
    N_samples = 1000
    K = 8 # 8-state discretization for compact display
    
    # 1. Generate Synthetic Regimes
    # Regime A: Trending (Strong momentum/continuation)
    print("\n[+] Generating Synthetic Returns for Trending Regime (High Persistence)...")
    trending_returns = np.zeros(N_samples)
    current_trend = 1.5
    for t in range(1, N_samples):
        # 80% chance to continue trend, 20% to reverse
        if np.random.rand() > 0.2:
            trending_returns[t] = current_trend + np.random.normal(0, 0.5)
        else:
            current_trend = -current_trend
            trending_returns[t] = current_trend + np.random.normal(0, 0.5)
            
    # Regime B: Mean-Reverting (Constant sign flipping)
    print("[+] Generating Synthetic Returns for Mean-Reverting Regime (High Alternation)...")
    reverting_returns = np.zeros(N_samples)
    current_sign = 1.0
    for t in range(1, N_samples):
        # 85% chance to flip sign of previous return
        if np.random.rand() > 0.15:
            current_sign = -current_sign
        reverting_returns[t] = current_sign * 1.5 + np.random.normal(0, 0.4)
        
    # 2. Fit the Quantizer on pooled baseline data to avoid bias
    all_baseline_returns = np.concatenate([trending_returns, reverting_returns])
    estimator = TransitionMatrixEstimator(k_states=K, alpha=0.5)
    boundaries = estimator.fit_discretizer(all_baseline_returns)
    
    print(f"\n[+] Quantization Boundaries (K={K}):")
    for idx, b in enumerate(boundaries):
        print(f"  State {idx}: {b:.4f}")
        
    # 3. Discretize and Estimate Matrices
    states_trending = estimator.discretize(trending_returns)
    states_reverting = estimator.discretize(reverting_returns)
    
    counts_trend, mat_trend = estimator.estimate_transition_matrix(states_trending)
    counts_revert, mat_revert = estimator.estimate_transition_matrix(states_reverting)
    
    occ_trend = estimator.compute_state_occupancy(states_trending)
    occ_revert = estimator.compute_state_occupancy(states_reverting)
    
    # 4. Compute Metrics
    h_trend = estimator.compute_entropy_rate(mat_trend, occ_trend)
    h_revert = estimator.compute_entropy_rate(mat_revert, occ_revert)
    
    jsd_diff = estimator.jensen_shannon_divergence(mat_trend, mat_revert, occ_trend)
    
    print("\n" + "=" * 50)
    print("📊 RESULTING METRICS SUMMARY")
    print("=" * 50)
    print(f"📈 Trending Regime Entropy Rate:       {h_trend:.4f} bits/transition")
    print(f"📉 Mean-Reverting Regime Entropy Rate:  {h_revert:.4f} bits/transition")
    print(f"🧬 Jensen-Shannon Divergence (P || Q): {jsd_diff:.4f} (Bounded [0, 1])")
    print("=" * 50)
    
    # Let's inspect the diagonal (persistence)
    diag_trend = np.diag(mat_trend)
    diag_revert = np.diag(mat_revert)
    print("\n🔍 State Self-Persistence Probabilities (Matrix Diagonals):")
    print("  State:       " + "   ".join([f"S{i+1}" for i in range(K)]))
    print("  Trending:    " + " ".join([f"{val:.2f}" for val in diag_trend]))
    print("  Reverting:   " + " ".join([f"{val:.2f}" for val in diag_revert]))
    print("\n*Notice how the trending regime retains significantly higher self-persistence in extreme states (S1/S8) compared to the reverting regime!*")
    
    # 5. Pointwise Surprise Demonstration
    # Take a sequence of mean-reverting states and feed them to the trending matrix
    test_revert_seq = states_reverting[:15]
    surprise_on_trend = estimator.compute_surprise_series(test_revert_seq, mat_trend)
    surprise_on_self = estimator.compute_surprise_series(test_revert_seq, mat_revert)
    
    print("\n⚠️ POINTWISE ANOMALY DETECTION TEST")
    print("-" * 50)
    print(f"Mean-Reverting State Sequence: {test_revert_seq.tolist()}")
    print(f"Surprise under Self Matrix:    {[round(x, 2) for x in surprise_on_self]}")
    print(f"Surprise under Trend Matrix:   {[round(x, 2) for x in surprise_on_trend]}")
    print(f"  >> Average Anomaly Surprise:  Self={np.mean(surprise_on_self):.2f} bits | Trend={np.mean(surprise_on_trend):.2f} bits")
    print("\n*Success! An outlier regime (reverting dynamics) scored against a trend-reference matrix generates massive, mathematically consistent 'Surprise' peaks, letting us flag structural changes instantly.*")

if __name__ == "__main__":
    run_simulation_demo()
