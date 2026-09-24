# Decision schedules and timing migration

Stockcast now decides **before current demand**. This owner-approved pre-release
change supersedes the former demand-first timing freeze. Existing runs can have
different orders, service, costs, and ending inventory under unchanged inputs.
Do not migrate by mechanically subtracting one from every lead time.

## Four independent choices

- `DecisionSchedule`: when a policy may request an order.
- `lead_time`: how many demand epochs until accepted goods are usable (`L >= 0`).
- Target construction and coverage: what inventory level is intended, for which dates.
- Policy: how to translate state and target into a requested order.

```python
from stockcast import PeriodicSchedule, OneTimeSchedule, ExplicitSchedule

weekly = PeriodicSchedule(every=7, start=0)
one_purchase = OneTimeSchedule(period=0)
irregular = ExplicitSchedule(periods=[0, 3, 10])
```

A schedule exposes `should_decide(period)`, `next_decision_period(period)`
(strictly later or `None`), and a JSON-serializable `to_manifest()`.
Custom deterministic schedules subclass `DecisionSchedule`; they cannot
inspect future demand. State-dependent ordering rules belong in the policy.
`PeriodicSchedule(1)` means every demand epoch. `R=0` is invalid.

`BasePolicy(lead_time=0, schedule=one_purchase, allow_backorders=False)`
requires neither a fake review interval nor a service probability. Existing
`review_period=R` constructors select `PeriodicSchedule(R, start=0)`.
Supplying both is permitted only for a matching periodic schedule.

You do not need to construct a scheduler object for ordinary periodic use:
`review_period=1` decides every demand period and `review_period=7` every seven
periods. The policy creates its periodic schedule internally. Notebook
[02b — Decision schedules](https://github.com/FilTheo/stockcast/blob/main/examples/notebooks/02b_decision_schedules.ipynb)
shows a timeline and real order records, including event-for-event equivalence
between the shorthand and an explicit periodic schedule.

## Coordinates and information

Schedules and `policy_schedule` keys use zero-based **demand periods within
this run**. Demand period 0 is the first demand row; its date remains
`opening_date + frequency`. State/event/callback periods remain
`opening_period + demand_period + 1`. Callback date/period tables still use
those state coordinates. A run manifest records both conventions.

A rolling snapshot for demand period `k` must have forecast origin
`opening_date + k * frequency`, the last observed demand date, not the date of
current demand. The forecast starts one offset after that origin. Fit outside
the engine using only data available by that cutoff. The engine validates
metadata but cannot prove the caller's actual training history.

The engine resets current flow fields before prediction and passes policies a
defensive state copy. It never presents current demand to a policy before its
decision. Snapshots must retain policy class, lead time, decision schedule,
shortage mode, and target probability configuration.

## Targets for recurring and irregular decisions

`OrderUpToPolicy` still requests `max(0, S-IP)`. For a periodic schedule,
its fitted `protection_horizon` must be `L+R`; a fixed target may intentionally
be reused over the replay. Setting `L=0, R=1` admits a one-period target.

For a nonperiodic schedule with next opportunity `u`, the target must cover
`u-t+L` periods from the current demand epoch. Supply snapshots for different
windows through `policy_schedule`. Nonperiodic targets require the exact
information origin and end date at each decision. If there is no next
opportunity, provide the terminal coverage horizon explicitly at fitting.
Ending a simulation or disabling settlement decisions does not automatically
retarget an otherwise recurring policy to a finite-horizon optimum.

External planner targets can use `service_level=None` and no
`target_probability`. Quantile-labelled columns require a declared probability;
independent-normal construction still requires one. No marginal quantile
summation or invented uncertainty is permitted.

## One season and newsvendor

`SingleOrderPolicy(lead_time=L, selling_horizon=H, decision_period=0,
allow_backorders=False)` orders once to an external target. The season starts
at receipt, demand period `decision_period+L`, and has `H` demand epochs.
Its target end date is `forecast_origin + (L+H)*frequency`. Demand outside the
season must explicitly be zero, and the simulation must observe the season's
end. Existing pipeline must arrive by season start so late goods cannot
suppress the purchase. Terminal stock is retained for explicit evaluation;
no automatic salvage transaction or revenue metric is introduced.

One weekly row can represent a one-week season (`H=1`); seven daily rows use
the distribution of total seven-day demand (`H=7`). The latter is cumulative
because the economic opportunity spans seven days, not because of a minimum
lead time. For positive lead time, include explicit pre-season zero-demand
epochs or supply an already-received opening state as appropriate.

`newsvendor_critical_fractile(selling_price=p, purchase_cost=c, salvage_value=v)`
returns `(p-c)/(p-v)` for `p>c>v>=0`. The caller obtains the corresponding
quantile of season demand and fits the policy with that probability. This
helper assumes classical linear lost-sales economics without additional
penalties or holding costs. Constraints, expiry, opening stock, backlog, or
within-season replenishment can change the optimization problem. A schedule
is not an optimizer. See Notebook 04d for executable discrete-demand examples.

## Migration checklist

- Replace `initial_decision="before_first_demand"` with a schedule starting at
  demand period 0. The old value raises an explanatory error. `"none"` is
  retained as the default neutral compatibility argument; it does not suppress
  scheduled first-period decisions. Use `PeriodicSchedule(R, start=R)` to delay
  the first opportunity. Pre-run purchases belong in explicit opening pipeline.
- Review lead times, opening state, and review phase together. An old opening
  purchase at the previous date is not the same as a new first-demand decision.
- Move rolling snapshot keys to zero-based demand coordinates and ensure their
  origins exclude current demand. Review callback tables separately: they
  retain state periods and dates.
- `on_after_prediction` now precedes demand; `on_after_demand` remains after
  demand and affects subsequent decisions. Constraints still follow order
  callbacks. Physical adjustments cannot retroactively satisfy lost sales.
- In manual loops, use `advance_period`, predict/apply orders if eligible, then
  `fulfill_demand`. `process_demand` remains a combined receipt/demand primitive
  for steps without an intervening decision.
- Re-execute notebooks and re-establish numerical baselines. Older rendered
  outputs and release-audit counts are historical, not new-engine evidence.

For the derivation, primary literature, software comparisons, and explicit
limits, read [Engine design and scientific basis](engine-design.md).
