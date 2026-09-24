# Compare scenarios fairly

An inventory comparison should vary the declared question while holding the
remaining scenario fixed. `SimulationEngine.run_comparison(...)` materializes a
callable demand source once and isolates copied policies and inventory state for
each branch.

Use the same demand path when comparing forecasts or policies. State which
input changes, keep lead time, costs, and windows explicit, and interpret
results on the same scoring window. Notebook 06 demonstrates direct cumulative
upper-quantile targets from Smooth, a fixed forecast-input comparison, and a
policy comparison in which `(R,S)` and `(s,Q)` consume the same `L+R` target
horizon. It separately inspects realized cumulative demand at the forecast
origin, then screens the simulated outcomes against a declared per-SKU fill-rate
floor and cost rates. One origin and one demand replay cannot establish
forecast calibration or a generally preferred policy.

For portfolio metrics, name the aggregation grain. For example, a fill rate is
demand weighted, while a stockout-period rate answers a calendar-period
question. Cost results require their active components and rates to be supplied
explicitly.
