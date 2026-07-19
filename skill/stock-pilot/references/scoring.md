# Decision Rules

The helper creates a reproducible baseline from trend, RSI, MACD, relative
strength, support/resistance, ATR, VIX, and data gates. It downgrades to `等待`
when expected reward/risk is below 1.5. The agent may refine the narrative but
must not relax freshness gates, invent probabilities, or override valuation blocks.
