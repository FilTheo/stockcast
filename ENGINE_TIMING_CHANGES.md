# Engine timing redesign: decisions, rationale, and evidence

Implementation completed locally on 2026-09-24. The owner approved replacing
the old default timing before release, migrating existing examples, and
documenting numerical changes. This document records that implementation;
it is not publication approval or a claim of universal policy optimality.

## Why the core changed

The old engine coupled periodic review, positive lead time, and an extra
opening decision. Its built-in order-up-to target required at least two demand
periods. That made immediate weekly replenishment and a single selling season
awkward to express through the standard policies.

The new design separates four questions: when an order may be requested,
when accepted goods arrive, which demand window a target represents, and how
the policy computes an order. Users can supply forecasts or planner targets
externally and extend scheduling and policies independently.

## Implemented contract

At demand epoch `t`, the engine expires unusable stock, advances the calendar,
receives due goods, makes any scheduled decision, applies callbacks and
constraints, receives accepted zero-lead goods, fulfills current demand, and
records the validated closing event. Due and immediate receipts serve old
backlog before current demand. FIFO immediate receipts receive the current
receipt date.

- Lead time is an integer `L >= 0`. An order accepted at `t` is usable before
  demand at `t+L`; zero lead means immediate receipt within that epoch.
- `DecisionSchedule` is independent of the policy's order calculation.
  `PeriodicSchedule`, `OneTimeSchedule`, and `ExplicitSchedule` implement it;
  custom deterministic schedules can subclass it. Periodic `R` remains positive.
- Schedule and rolling-policy snapshot keys are zero-based demand periods
  within a run. State, callback, and event periods retain their opening-state
  offset. Manifests describe both coordinate systems.
- Predictions receive a defensive pre-demand state. Current demand is not
  exposed before the decision. Forecast origin denotes the last observed
  demand date. Scientific metadata validation cannot prove external training
  data were actually restricted to that date.
- Periodic `OrderUpToPolicy` retains `max(0, S-IP)` and `H=L+R`. Irregular
  targets are checked against the next opportunity and their dated coverage;
  terminal coverage must be explicit. Planner targets need no invented
  probability, while quantile targets retain probability/provenance checks.
- `SingleOrderPolicy` buys once for an explicit selling season. It supports
  externally calculated targets and an optional classical economic critical-
  fractile helper. It checks season bounds, pre-arrival demand, and opening
  pipeline timing. It does not automatically liquidate stock or compute profit.

Schedules describe opportunities; state-dependent ordering decisions belong
in policies. They do not replace forecasting, optimization, constraints, or
the experiment's terminal accounting choices.

## Scientific basis and its limits

For a periodic decision at `t`, the next decision is at `t+R`, whose order
arrives before demand at `t+R+L`. Demand epochs `t` through `t+R+L-1` therefore
give a protection window of `L+R`. This covers inventory position, including
pipeline; it does not make today's positive-lead order available early.
The periodic-review protection interval is consistent with
[MIT's periodic-review notes](https://ocw.mit.edu/courses/esd-260j-logistics-systems-fall-2006/8b53c45fd26ffff706d815131e8d177e_lect11.pdf).
Zero lead time is also an established modeling case, including constrained
lost-sales models: [Dubois, Allaert and Witlox (2013)](https://doi.org/10.1016/j.orl.2013.10.006).

For a deterministic next opportunity `u`, the same counting argument yields
`H=u-t+L`. This is a derivation from the implemented convention, not an
optimality theorem for arbitrary irregular policies. Unknown future decision
dates or stochastic lead times need additional modeling and are not silently
translated into this formula.

The old end-of-period convention can legitimately yield `L+R-1` future demand
epochs. Event ordering determines the offset; that expression is not inherently
unscientific. [Stockpyl's sequence-of-events discussion](https://stockpyl.readthedocs.io/en/latest/tutorial/tutorial_sim.html#sequence-of-events)
and [SimOpt's recurrent inventory model](https://simopt.readthedocs.io/en/development/models/sscont.html)
illustrate differing conventions. There is no blanket trajectory-preserving
lead-time conversion when information, opening decisions, expiry, or callbacks
also change. The original author's motivation could not be established from
the reviewed repository evidence.

For classical linear lost-sales newsvendor economics, price `p`, purchase cost
`c`, and salvage `v` give the critical probability `(p-c)/(p-v)` under
`p>c>v>=0`. See [Stockpyl's economic newsvendor implementation](https://stockpyl.readthedocs.io/en/latest/_modules/stockpyl/newsvendor.html)
and [SimOpt's newsvendor model](https://simopt.readthedocs.io/en/latest/models/cntnv.html).
Additional costs, constraints, expiry, or replenishment can change the optimum.
A week or month can be one aggregate demand observation or several daily
observations; equivalent seasonal targets require the same total-demand law
and compatible physical/economic assumptions. Never sum marginal quantiles.

Protection-window validity is distinct from forecast calibration and policy
performance. In particular, positive-lead lost-sales base-stock policies need
not be optimal; see [Janakiraman and Roundy (2004)](https://doi.org/10.1287/opre.1040.0130).
An input target probability is not a guaranteed achieved fill rate. The more
detailed source assessment is in
[knowledge/94_decision_timing_research.md](knowledge/94_decision_timing_research.md).

## Compatibility and retained functionality

This is a deliberate default semantic migration. The old
`initial_decision="before_first_demand"` value now raises migration guidance;
use a schedule starting at demand period zero. Earlier purchases belong in
opening pipeline. `initial_decision="none"` remains a neutral argument, not a
request to suppress scheduled decisions. Manual loops now explicitly advance,
decide/apply orders, then fulfill demand.

Multiple SKUs, lost sales, backorders, positive-lead pipelines, quantity
constraints, FIFO expiry, typed callbacks, rolling forecasts, experiment
windows, comparison isolation, and cost/service evaluation remain supported.
Accounting tests cover stock, pipeline, backlog, receipts, fulfillment,
shortages, expiry, and adjustments. Events add `decision_inventory_position`
so order audits use the actual pre-demand decision state.

Unchanged parameters can produce different orders, stock, costs, and service.
The README example now orders 35 units rather than the old 39. Existing
notebooks were migrated and re-executed; the user's prior notebook 04 source
edits were retained. Notebook 04d now focuses on an empirical demand forecast
estimated from artificial history, its economic newsvendor quantile, and one
purchase for a held-out week. Its initial calendar-resolution comparison was
replaced at the owner's request with this forecasting-focused example.

## Verification

- Final repository-environment unit suite: **200 passed**, 318.18 seconds.
  An earlier Python 3.12 full-suite run passed 199 tests before the final
  validation-isolation regression was added; it is not the final 200-test run.
- All **13 notebooks** executed successfully against current source. Before
  saving outputs, each executed code cell was checked for exact equality with
  its current notebook source. The operational notebooks were rerun after
  their final planning/forecast-cutoff edits.
- Strict MkDocs build passed. Source-import README/artifact smoke passed.
- Fatal-error Ruff checks and `git diff --check` passed. This does not claim
  the pre-existing repository-wide style backlog was cleared.

The core tests include independent calendar oracles for `L=0,1,2` and
`R=1,2,3` in both shortage modes, immediate FIFO receipts, old backlog,
constraints, irregular windows, delayed seasons, repeated runs, and prevention
of current-demand leakage or policy mutation of physical state.

Exact commands and environment details are in
[knowledge/95_decision_timing_implementation.md](knowledge/95_decision_timing_implementation.md).
These are source-level results. Wheel/sdist rebuilds, clean installed-artifact
checks, and hosted CI release gates still need to be repeated before release.

## Documentation and next work

Follow-up on 2026-09-24: added the compact `02b_decision_schedules.ipynb`
notebook, bringing the collection to 14. It shows five schedules on one
timeline, compares actual orders, and verifies that `review_period=3` and
an explicit periodic schedule produce identical events. Its execution and
the strict documentation build passed. No engine changes were needed.

Current public API, timing, forecast, callback, event-schema, production-flow,
release-note, and notebook documentation has been migrated. Internal execution,
contract, module, and test-evidence pages were updated. The practical migration
guide is [docs/concepts/decision-schedules.md](docs/concepts/decision-schedules.md).
The public [Engine design and scientific basis](docs/concepts/engine-design.md)
now explains the tested contract, exact event sequence, horizon derivation,
information boundaries, accounting identities, primary papers, and limits.
Source attribution was rechecked in knowledge 96. No archive, package identity,
version, or publication boundary was changed.
