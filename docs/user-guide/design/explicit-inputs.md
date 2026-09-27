# Every input is explicit

*A deep dive from our [Philosophy](../../get-started/philosophy.md).*

**Decision.** Opening stock, dates, frequency, lead time, review timing,
shortage rule, run windows, costs, forecast uncertainty, and the demand's
provenance are all supplied by you. When one is missing or inconsistent,
Stockcast stops and says which one, instead of assuming a value.

## Why

**Results should rest on facts you chose.** A simulation that starts from
"probably zero backorders", fills missing days with zero demand, or invents a
safety stock of 30% of the mean produces numbers that look precise but
describe an operation nobody specified. Asking for each input keeps every
result traceable to a decision you made.

**Uncertainty belongs to the forecast.** How uncertain demand is, and how
that uncertainty spreads over a window, is exactly what a forecasting model
estimates. Stockcast asks for it (a quantile, sample paths, or standard
deviations) rather than guessing it from the mean.

**Early, clear messages save time.** Everything that can be checked is checked
before the first period runs: complete demand calendars, matching dates,
target windows, pipeline lengths. A problem surfaces in a second, with its SKU
and period, not as a strange metric at the end of a long experiment.

**Experiments become reproducible.** Because nothing is implicit, the run
manifest can record everything that shaped a result: demand fingerprint and
seed, policy and target metadata, opening state, settings, and versions.

## What it means for you

- Nothing is guessed. What Stockcast can read from your inputs, it reads: the
  run length from the demand table, the period length from the policy, and a
  target's probability and window from the policy's service level, lead time,
  and review period. Anything it cannot read, such as the forecast origin or
  the uncertainty, you state. If you also pass a value it can read, the two
  must agree.
- A run scores every period unless you set warm-up or settlement windows,
  and the manifest records the windows it used.
- Costs are only used when you give their rates, zeros included, so a total
  cost always means exactly the components you listed.
