# Stock Pilot Data Freshness Policy

No layer may be described as current merely because a request succeeded. Every
layer must record its source timestamp and prove it is the latest value that the
source currently publishes.

| Layer | Current-data rule | Failure behavior |
|---|---|---|
| Price and candles | Latest completed US trading session; quote rechecked at collection time | Block action |
| SEC filings | Official `data.sec.gov/submissions` index checked during the run | Block action |
| Financials | Latest applicable 10-Q/10-K, earnings 8-K Item 2.02, 20-F, or 6-K | Block action |
| Valuation | Current quote plus latest four quarters; flag non-recurring EPS | Block valuation conclusion |
| VIX | Current Cboe delayed quote; FRED is history only | Block macro conclusion if absent |
| Treasury yields | Latest US Treasury daily yield-curve release | Reduce/block macro conclusion |
| Other daily macro | Latest published observation, maximum three calendar days around weekends | Reduce/block macro conclusion |
| Monthly macro | Latest published release, maximum 50 days | Reduce/block macro conclusion |
| News | At least one relevant item in the last seven days | Block catalyst confidence |
| Catalysts | Only future eight-week window; label confirmed or estimated | Exclude invalid event |

Run `tools/freshness_audit.py` after collection. If it returns
`BLOCK_ACTION`, suppress buy/hold/sell output and issue `数据不足/数据过期`.
