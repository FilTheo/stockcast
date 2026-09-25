# 40 — Policies and forecast targets

## 40.1 Shared policy contract

Every policy derives from `BasePolicy` and declares:

- integer `lead_time >= 0`;
- a `DecisionSchedule`, or positive `review_period` as periodic shorthand;
- optional service level strictly between zero and one;
- an explicit boolean backorder mode.

A policy must be fitted before simulation. `fit` creates and validates target
state; `predict` receives live inventory and returns an `OrderDecision`. The
engine controls review timing and order mutation.

## 40.2 Inventory position

All replenishment rules use:

```text
inventory_position = on_hand + sum(in_transit) - backorders
```

This is different from current physical stock. An order changes inventory
position through accepted stock immediately; zero-lead orders are received in
the decision epoch and positive-lead orders remain in the pipeline.

## 40.3 Target metadata shared across policies

Target validation requires the forecast target to be traceable to:

- an exact SKU;
- forecast origin;
- forward frequency;
- target horizon;
- target end date;
- target probability matching the policy service level;
- an accepted source/aggregation mode.

Quantile-looking labels such as `q95` are parsed and checked against the stated
probability. Direct cumulative targets must declare source
`external_direct`. Target end date must equal origin plus horizon times the
declared offset. One finite nonnegative target is required per SKU.

Marginal quantile summation is rejected. In general,
`sum(Q_p(D_t))` is not the `p` quantile of cumulative demand, so that shortcut
must not reappear under a renamed column.

## 40.4 Order-up-to `(R,S)`

`OrderUpToPolicy` reviews every `R` periods and protects a horizon:

```text
H = lead_time + review_period
requested_order = max(0, S - inventory_position)
```

Exactly one fitting mode is accepted:

### Direct cumulative target

The caller supplies one target `S` per SKU for horizon `H`, with explicit
probability, `external_direct` source, origin, frequency, and end date. An
`aggregation_method` is not accepted because the target is asserted to be
directly calculated outside the policy.

### Independent-normal moments

The caller supplies consecutive per-step mean and standard deviation rows for
forecast horizons `1..H`. Dates must match every horizon step. The policy uses
the explicit independence assumption:

```text
S = sum(step_means) + z(service_level) * sqrt(sum(step_std_deviation^2))
```

This is the only built-in distributional aggregation. It must be labeled as an
independence assumption and is not a general uncertainty model.

## 40.5 Reorder-point `(s,Q)` and `(s,S)`: `ReorderPointPolicy`

Owner-approved on 2026-09-24, replacing `ContinuousReviewPolicy`. Stockcast has no
continuous physical-time review; a reorder-point rule is combined with a
`DecisionSchedule`. The rule decides how much, the schedule decides when.
`review_period=1` is every-period review.

```text
if inventory_position <= s: (s,Q) requests Q; (s,S) requests max(0, S - IP)
else:                        requested_order = 0
```

Window of `s` (same derivation as `(R,S)`): if the rule does not order at `t`
and the next opportunity is `u`, the next order arrives before demand `u+L`, so
the position at `t` is exposed to `t..u+L-1`:

```text
H = (u - t) + L      periodic: L + R      every period: L + 1
```

- Fit requires `reorder_horizon == L + R` for periodic schedules. Other schedules
  declare it explicitly, and it is checked before the run against each decision's
  next opportunity (shared `validate_schedule_coverage`, also used by
  `OrderUpToPolicy`). Nonperiodic targets need the exact information origin,
  so irregular schedules normally use per-decision snapshots.
- The end date is `forecast_origin + reorder_horizon` periods.
- Quantile mode (`service_level` set) requires an equal `target_probability`
  and label-consistent quantile columns. Planner mode (`service_level=None`)
  forbids a probability and quantile-labelled columns.
- An `L`-window quantile is a justified basis for `s`, not a realized-service
  guarantee. Undershoot, `Q`, outstanding orders, the shortage mode, the service
  measure, and the demand process all matter. The per-review statement
  (backorders, independent demand, no order at `t`) is only a conditional
  lower bound.
- `Q` is explicit, positive, and sourced. `S` is an external policy level
  (`order_up_to_representation="external_policy_level"`) with only `S >= s`
  validated. It has no probability or horizon because `s,S` are generally
  jointly determined (Zheng & Federgruen 1991, used by Stockpyl's periodic
  `(s,S)` optimizer).
- Rolling snapshots must match class, lead time, schedule, service level and
  shortage mode (engine check). Policy type and `Q` are not engine-checked:
  a generic attribute check would break custom policies that use the same
  attribute names for rolling fitted values.

Evidence and rationale: the old policy sized `s` over `L` only, with `s=0` forced
at `L=0`. That matched the pre-migration after-demand timing (`L+R-1` with
`R=1`), but not the current one. In a scratch audit (Poisson(5), backorders,
`Q=15`, target 0.95), `L`-sized `s` gave cycle service 0.76/0.79/0.88 for
`L=1/2/4`, versus 0.99/0.99/0.98 with `L+1`. At `L=0`, the forced `s=0` gave
fill 0.83 and cycle service 0.19. Executable evidence: stress tests
`test_every_period_reorder_point_needs_lead_plus_one_window` and
`test_irregular_schedule_window_is_next_opportunity_plus_lead`, unit tests in
`test_decision_timing.py`, and notebook 05b (daily review with an `L`-sized
`s` reaches cycle service 0.66). Stockpyl keeps continuous-review lead times
unchanged only because its simulator orders after demand. External citations
to Silver–Naseraldin–Bischak and a periodic base-stock chronology paper were
supplied by the owner's external review. They were not opened in this audit
(paywalled/PDF), so they are not cited in public docs.

## 40.6 Periodic review `(R,s,S)`

`PeriodicReviewPolicy` is eligible every `R` periods:

```text
if inventory_position <= s: requested_order = max(0, S - inventory_position)
else:                        requested_order = 0
```

Targets come from a `PeriodicReviewTargetProvider`. Built-in providers are:

- `ColumnPeriodicReviewTargets`: selects caller-named `s` and `S` columns from
  an external table;
- `FixedPeriodicReviewTargets`: uses explicit scalars or exact per-SKU maps.

Custom providers are allowed, but their output is centrally validated: exact
SKU coverage, one row per SKU, finite values, `s >= 0`, and `S >= s`. Provider
metadata and manifest data must be JSON-serializable so run provenance does not
depend on an opaque Python object.

## 40.7 Policy schedule

A run can provide refitted policies keyed to decision dates. This models target
updates without mutating one fitted policy during the run. Scheduled policies
must preserve the policy family and operational configuration; only fitted
target content may change. The schedule must cover valid decision periods and
each snapshot origin equals the information cutoff before its demand epoch.

## 40.7a Lead-time assumption under a supply model

Every protection window above uses the policy's fixed `lead_time`. A
`SupplyModel` can deliver at other times: different or random supplier lead
times, partial deliveries, or `DeliveryOutcome` delays
([97.7](97_open_orders_and_suppliers.md#977-supplier-delivery-outcomes)).
Owner decision 2026-09-25:

- targets are never adjusted, and no declaration argument is required;
- the engine issues one `UserWarning` per run the first time a delivery is
  scheduled or delayed off `policy.lead_time`;
- the run stays correct accounting of that target against that supply
  (typically a misspecification or robustness experiment);
- targets meant for random lead times are computed outside Stockcast over
  the random window and passed in as usual.

Short deliveries without delays do not trigger the warning. The mapping case
(one supplier, `lead_time == policy.lead_time`) never warns.

## 40.8 Safe extension checklist

Before adding another policy or uncertainty model, decide and test:

1. the precise protection horizon;
2. whether its target is a cumulative distribution, sample path, joint draws,
   or an approximation with named assumptions;
3. origin/frequency/end-date alignment;
4. service-level semantics and whether one probability is sufficient;
5. the exact trigger and order-quantity equation;
6. required target provenance and a stable fingerprint;
7. behavior under backlog and lost-sales modes;
8. event diagnostics needed for later audit.

Do not treat a convenient table shape as enough to establish those semantics.

## 40.9 Schedule and season extensions

`OrderUpToPolicy` accepts explicit schedules and optional probability for
externally supplied planner targets. Periodic targets retain `H=L+R`.
Nonperiodic targets are checked at each enabled opportunity against
`next_decision-period+L`; the final opportunity uses its explicit fitted
horizon. Nonperiodic origins/end dates must match the exact decision window.
No schedule implicitly constructs uncertainty or finite-horizon optimal targets.

`SingleOrderPolicy` uses `OneTimeSchedule`, a declared selling horizon, and an
external season target. The season starts at receipt; the end date is
origin plus `(L+selling_horizon)` offsets. Demand outside that season must be
explicitly zero; the run must observe its end, and pipeline arriving after
season start is rejected. A scalar critical-fractile helper supports classical
`p>c>v>=0` economics; demand quantile construction stays external.

Historical: `ContinuousReviewPolicy` with `L=0` required a zero reorder target
over a zero-duration window.

Resolved 2026-09-24: `ContinuousReviewPolicy` was replaced by
`ReorderPointPolicy`; see 40.5.
