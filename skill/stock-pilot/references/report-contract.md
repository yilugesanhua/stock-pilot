# Report Contract

Every run must render the same standard report sections, even when the quality gate
blocks an action recommendation. The report must contain: data status and as-of
dates, direct recommendation, entry/confirmation condition, invalidation and stop
basis, two targets, risk/reward, 2-8 week horizon, confidence, fundamentals,
valuation limits, technicals, macro/VIX, seven-day news, eight-week catalysts,
Bull/Base/Bear scenarios, risks, freshness audit, and clickable source links.

When `quality.json` is `BLOCK_ACTION`, use `数据不足` as the recommendation, write
`N/A` for price-dependent execution fields, and explain the failed gates and missing
layers. Do not return a shortened report and do not infer targets or stop levels.

When the recommendation is `回避`, `卖出`, or `减仓`, do not present a new-money
entry range, stop, targets, or reward/risk as if the report recommends a trade. Show
`N/A` for those fields and state the observable conditions required for reassessment.

`report.json` must preserve the existing analysis fields and add a
`standard_report` object with `schema_version: "1.0"` containing the same sections
in machine-readable form.

Allowed recommendations: `买入`, `分批买入`, `持有`, `等待`, `减仓`, `卖出`,
`回避`, `数据不足`. Without portfolio context, default to new-money actions only.
