---
name: performance-strategist
description: Analyze portfolio health and provide dynamic strategy directives (Anti-Greed, Bear Shield, Cash Rebalancing).
---

# Performance Strategist

This skill uses the `Performance` and `Positions` data to provide objective trading directives, ensuring the portfolio adheres to risk management and profit-taking rules.

## How to execute this skill:

1.  **Gather Data**:
    *   Read the `Performance` tab to get `overall_growth_pct`, `weekly_drawdown_pct`, and calculate `cash_percentage`.
    *   Read the `Positions` tab to get the list of active tickers and their `entry_price`.
2.  **Format Input**: Create a JSON string with the following structure:
    ```json
    {
      "portfolio_stats": {
        "cash_percentage": 4.5,
        "weekly_drawdown_pct": -1.2,
        "overall_growth_pct": 11.5
      },
      "positions": [
        {"ticker": "CRM", "entry_price": 166.09},
        {"ticker": "NVDA", "entry_price": 235.00}
      ]
    }
    ```
3.  **Run Engine**:
    `python3 skills/performance-strategist/strategy_engine.py '<JSON_STRING>'`
4.  **Action**: Present the report and follow the suggested directives for rebalancing or trimming.
