# Sprint Contract — Reject Capped / Insane Edge Candidates

## What We're Building
Implement rejection (not mere capping) for raw edge values that exceed MAX_EDGE_PCT (500%).
A capped edge indicates a noisy or broken model/price comparison and must not become an order.

## Done Criteria (testable)
1. `calculate_edge_pct()` exposes whether raw edge exceeded MAX_EDGE_PCT.
2. In live modes (micro-live, real-live): raw edge > MAX_EDGE_PCT rejects the market with log line.
3. In shadow mode: capped-edge candidates are NOT counted as safe would-orders.
4. Raw edge exactly at MAX_EDGE_PCT still passes (boundary test).
5. Sub-5c price floor still rejects before edge check.
6. Full test suite passes (429+ tests).
7. Shadow smoke shows 0 capped-edge "Would place order" lines.
8. Resting orders remain 0 after patch.
9. Live cron remains paused.
10. No secrets leaked in code or proof files.

## Regression List
- Existing valid edge below cap still works.
- Negative/zero edge still rejected.
- Price floor still applies.
- Category filter still works.
- Expiry filter still works.
- Daily loss guard still works.
- Run limits still work.
- All 429 existing tests still pass.
