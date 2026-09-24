# Engine design and scientific basis

Stockcast is a discrete-period inventory simulator. It evaluates a declared
policy against demand, opening stock, replenishment timing, and operational
rules. Forecasting and economic optimization can happen outside the engine;
the engine executes decisions and checks their physical accounting.

This page explains the model behind that execution. **Literature supports the
inventory models and timing conventions; Stockcast's API decomposition and
validation rules are engineering choices.** A source that supports periodic
review does not prove that every policy, callback, or constraint combination
is optimal.

## 1. Separate the decisions that describe a scenario

| Component | Question it answers | Stockcast interface |
|---|---|---|
| Demand and calendar | What demand occurs, and at what resolution? | Complete SKU-period data and an explicit frequency |
| Decision schedule | When may a policy request an order? | `review_period` shorthand or `DecisionSchedule` |
| Lead time | When does an accepted order become usable? | Nonnegative integer `lead_time` |
| Target or forecast | What stock level is intended, and which demand dates does it cover? | Fitted target data with provenance |
| Policy | How much should be requested given the current state? | `BasePolicy` implementations |
| Execution rules | What order is accepted, and how does stock change? | Constraints, typed callbacks, and engine transitions |
| Evaluation | Which outcomes and costs matter? | Validated events, explicit windows and costs |

This separation lets a user change ordering opportunities without rewriting
stock accounting, or use an external forecast without embedding a forecasting
library in the simulator. It does not make a target automatically appropriate
after its schedule changes.

For ordinary periodic decisions, `review_period=7` creates a
`PeriodicSchedule(every=7)` internally. Explicit periodic schedules add a start
offset; one-time and explicit-period schedules express finite or irregular
opportunities. Custom deterministic schedules implement eligibility, the next
opportunity, and serializable configuration. State-dependent decisions such as
“order only below the reorder point” belong in the policy. A schedule grants
an opportunity; it need not produce a positive order.

## 2. Declare the event sequence before interpreting lead time

Stockcast uses **receive, decide, then meet demand** within each epoch:

| Phase | Effect |
|---|---|
| Expire | `ShelfLifeEngine` removes lots unusable at the current date. More generally, inventory processes report `before_demand` flows here. |
| Open and receive | Advance the clock, reset current flow fields, receive due pipeline goods, and serve old backlog first. |
| Decide | At an enabled opportunity, select the fitted policy snapshot and predict from a defensive pre-demand state. |
| Accept | Apply order callbacks, then constraints in their declared order. Record requested and accepted quantities. |
| Receive immediately | Accepted `L=0` units arrive now, serving remaining old backlog first. Positive-lead goods enter the pipeline. |
| Meet demand | Serve current demand; record lost sales or add unmet demand to backlog. |
| Close | Apply `after_demand` process flows, then after-demand callback adjustments; reconcile lot/state accounting and validate the event row. |

An order accepted at demand epoch `t` arrives **before demand at `t+L`**.
Consequently, `L=0` means same-epoch supply and `L=1` means supply at the next
demand epoch. Policies are checked only at simulated period boundaries;
Stockcast has no continuous physical-time review.

The receive–review/order–demand sequence is explicit in section 3 of
[Zhu (2022), section 3](https://pmc.ncbi.nlm.nih.gov/articles/PMC8731212/).
That paper studies bounded independent identically distributed demand, full
backlogging, and order-quantity bounds. It supports this event convention;
its performance results do not transfer to all our shortage modes or extensions.

This convention is a modeling choice, not the only correct sequence.
[Stockpyl's simulation documentation](https://stockpyl.readthedocs.io/en/latest/tutorial/tutorial_sim.html#sequence-of-events)
describes demand observation before ordering and distinguishes order and
shipment lead times. [SimOpt's recurrent inventory model](https://simopt.readthedocs.io/en/development/models/sscont.html)
places orders at the end of a period and receives them at the beginning of
`n+l+1`. These examples explain why a parameter called “lead time” cannot be
transferred between simulators without checking event order.

Zero lead time is an established inventory modeling case. Dubois, Allaert and
Witlox study periodic review with zero lead time, lost sales, and capacity
constraints. Their analytical results apply to that paper's assumptions;
they do not certify arbitrary Stockcast extensions.
[Dubois et al. (2013), DOI](https://doi.org/10.1016/j.orl.2013.10.006).

## 3. Derive the protection horizon from the sequence

At a periodic decision epoch `t`, the next opportunity is `t+R`. An order
placed there arrives before demand at `t+R+L`. Inventory position at today's
decision therefore protects the demand epochs before that future arrival:

```text
covered demand: t, t+1, ..., t+R+L-1
number of epochs: H = L + R
```

This is the familiar lead-time-plus-review-period protection interval in
[Graves' MIT supply-chain summary, slide 5](https://ocw.mit.edu/courses/15-763j-manufacturing-system-and-supply-chain-design-spring-2005/e1800430fdccaf618b22bd551fc00fac_summary.pdf).
Stockcast's event-counting argument above explains its exact endpoints.

| Lead time `L` | Review interval `R` | Protected demand epochs | Meaning |
|---:|---:|---|---|
| 0 | 1 | `t` | Immediate supply, one demand epoch until the next replenishment opportunity |
| 1 | 1 | `t, t+1` | Today's order arrives at `t+1`; the next decision's order arrives at `t+2` |
| 2 | 3 | `t` through `t+4` | The next decision is at `t+3`, with arrival at `t+5` |

The order-up-to rule requests `max(0, S-IP)`, where `IP` is on-hand stock plus
pipeline minus backlog. **Protection is a statement about inventory position,
not permission to use goods before arrival.** In the second row, demand at
`t` still requires opening or previously ordered stock. A large target cannot
repair a shortage that occurs before its replenishment arrives.

For a deterministic next opportunity `u>t`, the same derivation gives
`H=u-t+L`. Nonperiodic order-up-to targets must match those dated windows;
different gaps may require different fitted snapshots. When no next
opportunity exists, terminal coverage must be explicit. This irregular-window
formula is a derivation from our convention, not an optimality result from a
paper. A future contingent decision time or random lead time does not have
this single deterministic horizon.

The same window applies to a reorder point. If `ReorderPointPolicy` does not
order at `t`, nothing can arrive before demand `u+L`, so its position at `t`
must cover `t, ..., u+L-1`. Every-period review therefore protects `L+1`
epochs, one more than the lead-time demand of continuous-review formulas. This
extra period is the cost of reviewing only at period boundaries. Stockpyl's
simulator keeps continuous-review lead times unchanged only because it orders
after observing demand
([sequence of events](https://stockpyl.readthedocs.io/en/latest/tutorial/tutorial_sim.html#sequence-of-events)).
Under Stockcast's before-demand order, a reorder point sized for `L` alone
under-protects. In Notebook 05b, daily review with the 2-day lead-time
quantile reaches a cycle service of 0.66 against a 0.95 target. With `L=0`,
it would leave the current epoch's demand unprotected. The window quantile is the basis for `s`, not a service
guarantee.

The former demand-first implementation can legitimately yield an `L+R-1`
future-exposure count. The redesign moved the decision, information cutoff,
and first opportunity together. It did not establish that the old offset was
mathematically invalid. Do not migrate by blindly subtracting one from lead
times; follow the [migration guide](decision-schedules.md).

## 4. Keep future observations out of decisions

Demand period 0 is the first demand row, dated one frequency offset after
the opening state's date. Schedule and `policy_schedule` keys use these
zero-based run coordinates. State, event, and callback periods instead equal
`opening_period + demand_period + 1`.

A forecast origin means **demand observed through that date**. At a decision
before today's demand, the matching rolling forecast origin is the preceding
demand date. For example, with an opening state dated Monday and daily demand,
Tuesday's first decision uses information through Monday. Tuesday's realized
demand becomes available only afterward.

The engine materializes and validates the entire demand calendar before
execution, but does not pass current demand to policy prediction. It resets
current flow fields, uses defensive state copies, and isolates optional
demand-window validators from the prediction instance. These boundaries
prevent the supported API from inadvertently revealing future observations;
they are not a security sandbox for arbitrary user Python code.

`policy_schedule` updates fitted information at eligible decisions; it is
distinct from the decision schedule itself. A reused fixed target remains a
static-target experiment, not a fresh forecast at every review. The engine
checks declared origins, horizons, probabilities and dates. Users remain
responsible for the actual upstream training cutoff and calibration.

For multi-period uncertainty, forecast the distribution of total demand.
With joint predictive paths, sum each path and then take the quantile across
totals. Marginal quantiles cannot generally be added. The independent-normal
route requires explicit independence/normality assumptions and supplied
uncertainty; dependent errors require covariance or an appropriate joint
forecast. See [forecast targets](forecast-targets.md).

## 5. A newsvendor season is not a review interval

`SingleOrderPolicy` makes one purchase for an explicit season. Its season
starts at receipt and lasts `selling_horizon` demand epochs. A weekly row can
represent a whole selling week; seven daily rows require a target for total
seven-day demand. Positive lead time requires an explicit pre-season delivery
wait with zero demand, so delivery precedes the selling opportunity.

For classical linear lost-sales economics with selling price `p`, purchase
cost `c` and terminal salvage `v`, the economic probability is
`alpha=(p-c)/(p-v)`. The helper requires `p>c>v>=0`; the caller supplies the
corresponding demand quantile. This follows the classical economic newsvendor
formulation implemented in
[Stockpyl's newsvendor routines](https://stockpyl.readthedocs.io/en/latest/_modules/stockpyl/newsvendor.html).
It does not solve models with arbitrary extra costs, expiry within the season,
capacity constraints, or later purchases.

The policy checks complete season coverage, zero demand outside it, and
opening pipeline that arrives by season start. Ending the season does not
delete stock, cancel pipeline, or automatically post salvage revenue. The
[04b newsvendor notebook](../tutorials/notebooks.md) illustrates a forecast
distribution fitted to artificial history, one economic quantile, and a
held-out selling week with explicit profit accounting.

## 6. Physical accounting is independent of the policy

The ledger checks the following identities for each SKU-period. `fulfilled`
means service of current demand; old backlog fulfillment is separate.

```text
demand = fulfilled + shortage
ending_on_hand = starting_on_hand + receipts
                 - old_backlog_fulfilled - fulfilled - expired + adjustment
ending_backlog = starting_backlog + new_backlog - old_backlog_fulfilled
ending_pipeline = starting_pipeline + accepted_order - receipts
ending_inventory_position = ending_on_hand + ending_pipeline - ending_backlog
```

When a run's [inventory processes](../guides/physical-processes.md) declare
general flows, the on-hand identity also adds `process_inflow` and subtracts
`process_outflow`; expiry processes add into `expired`.

In lost-sales mode, shortages leave the system; in backorder mode they enter
backlog. Immediate supply appears in both accepted orders and receipts, so it
leaves no new pipeline. Old backlog retains priority over current demand.
After-demand stock additions cannot retroactively erase lost sales.

FIFO shelf life is measured in **calendar days**, not demand periods. A lot
expires when its age at the current demand date reaches `shelf_life_days`.
Immediate receipts form lots dated today. The FIFO ledger must reconcile
with aggregate physical stock. These are explicit Stockcast conventions;
changing expiry order or service priority describes a different model.

Constraints apply after order callbacks and before receipt, including for
zero lead time. The event ledger preserves raw, callback-adjusted, constrained,
and accepted quantities. `decision_inventory_position` records the actual
pre-demand state; ending inventory cannot reconstruct it by simply subtracting
the order. See [events and evaluation](events-and-evaluation.md).

## 7. Initialization, evaluation, and reproducibility

Opening stock, pipeline, backlog, dates, and shortage mode are declared inputs.
The engine does not infer an opening stock level from forecasts. Warmup advances
state; scoring defines the evaluated window; settlement observes tail effects
and disables new decisions by default. A recurring policy is not automatically
converted to a finite-horizon optimum when the run ends.

Purchases are charged when ordered. A finite-season profit calculation must
explicitly choose its cost window and treatment of leftovers, pipeline, and
backlog. Stock remaining at the end is still physical stock unless the caller
defines a separate terminal valuation.

Comparisons materialize a common demand path once and isolate mutable stock,
policies, and constraints between scenarios. Callback instances are reset for
each execution; their accepted effects are recorded. Manifests preserve input
fingerprints, configuration, timing, and available dependency/source provenance.
A random seed alone cannot reproduce an experiment whose inputs or external
forecast procedure have changed.

## 8. What the evidence establishes

| Evidence | What it establishes | What it does not establish |
|---|---|---|
| Inventory literature | Legitimate models, assumptions, and protection/economic formulas | Correctness of our Python implementation |
| Independent calendar oracles | Agreement of orders, arrivals and stock/backlog flows with the declared sequence | Optimality or forecast calibration |
| Accounting validation | Conservation identities and coherent recorded adjustments | Whether scenario inputs represent a real operation |
| Executed notebooks | Runnable forecast-to-decision and extension workflows | General empirical superiority |

The [timing tests](https://github.com/FilTheo/stockcast/blob/main/tests/unit/test_decision_timing.py)
include `L=0,1,2`, `R=1,2,3`, both shortage modes, irregular and one-time
schedules, immediate FIFO receipts, constraints, repeated runs, and information
isolation. The separate
[delivery-calendar reference tests](https://github.com/FilTheo/stockcast/blob/main/tests/unit/test_inventory_reference_model.py)
check positive-lead execution and evaluated costs.

`H=L+R` does not prove that an order-up-to policy is optimal under lost sales.
Janakiraman and Roundy analyze base-stock policies in a lost-sales setting
where they are generally suboptimal.
[Janakiraman and Roundy (2004), DOI](https://doi.org/10.1287/opre.1040.0130).
Likewise, a target quantile probability is not a guaranteed achieved fill rate.

The current core models deterministic integer lead time and one demand
realization per SKU-period. Stochastic lead times, arbitrary within-period
event queues, typed multi-supplier orders, and general constrained optimal
control are not established capabilities. The decision scheduler controls
ordering eligibility; it is not a general discrete-event simulation scheduler.

Use the [schedule guide](decision-schedules.md) for practical configuration,
the [system overview](system-model.md) for the short event sequence, and the
[API reference](../reference/core.md) for exact signatures.

## Primary reading

The links beside each claim identify its evidence. For bibliographic use, the
research papers are:

- Zhu, H. (2022). *A simple heuristic policy for stochastic inventory systems
  with both minimum and maximum order quantity requirements.* Annals of
  Operations Research, 309, 347–363.
  [DOI: 10.1007/s10479-021-04441-1](https://doi.org/10.1007/s10479-021-04441-1).
- Dubois, T., Allaert, G., and Witlox, F. (2013). *Determining the fill rate
  for a periodic review inventory policy with capacitated replenishments,
  lost sales and zero lead time.* Operations Research Letters, 41(6), 726–729.
  [Author manuscript](https://backoffice.biblio.ugent.be/download/4284439/6809517),
  [DOI: 10.1016/j.orl.2013.10.006](https://doi.org/10.1016/j.orl.2013.10.006).
- Janakiraman, G., and Roundy, R. O. (2004). *Lost-Sales Problems with
  Stochastic Lead Times: Convexity Results for Base-Stock Policies.* Operations
  Research, 52(5), 795–803.
  [DOI: 10.1287/opre.1040.0130](https://doi.org/10.1287/opre.1040.0130).
  Their model orders before receiving shipments, then observes demand; we cite
  its lost-sales policy limitation, not an identical event sequence or support
  for stochastic lead time in Stockcast.

The MIT slides are primary teaching material; Stockpyl and SimOpt links are
official software documentation/source. They serve different purposes from
the research papers. References were checked on 2026-09-24.
