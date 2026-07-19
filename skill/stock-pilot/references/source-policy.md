# Source Policy

1. Current price and candles: Longbridge, latest completed US session.
2. Filing index: official `data.sec.gov/submissions`.
3. Domestic financials: SEC Company Facts matched by accession and period.
4. FPI financials: content-verified 6-K/20-F; standardized Longbridge values only when periods align.
5. Current VIX: Cboe delayed quote. FRED VIX is historical context only.
6. Current yields: US Treasury daily curve. Other macro series: latest FRED release.
7. News: Longbridge metadata; X/Reddit are supplementary clues and never primary facts.

If a core source cannot prove freshness, set `BLOCK_ACTION`.
