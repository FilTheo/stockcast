# Stockcast examples

The notebooks in `notebooks/` are runnable examples for a repository clone.
Run them from the repository root after installing Stockcast and any
notebook-specific optional dependencies.

`notebooks/data/m5/` contains the small M5-derived assets used by Notebooks 07,
09, and 10. They are versioned for reproducible GitHub examples but are
intentionally excluded from PyPI source distributions and wheels. See the
nearby data README for attribution and scope.

Notebook `02b_decision_schedules.ipynb` is a compact visual tour of periodic,
delayed, one-time, and irregular decisions. It also demonstrates using
`review_period` without constructing a scheduler object.

The six Notebook 04 tutorials progress from one-week Smooth forecasts and
weekly orders (`04`) to a daily economic newsvendor (`04b`), then to a fixed
cumulative protection target (`04c`), rolling cumulative updates (`04d`),
three cumulative uncertainty methods (`04e`), and an end-to-end scheduled
forecast-to-order simulation (`04f`). Each lesson keeps forecast inputs,
inventory decisions, and held-out outcomes visible.

Notebook `05_custom_policies.ipynb` writes a familiar `(s,Q)` rule to teach the
`BasePolicy` contract, tunes its fixed quantity against explicit costs, then
shows a small change to the rule and checks both on held-out demand. The
ordinary `(s,Q)` rule is also available as `ReorderPointPolicy`.

Notebook `05b_reorder_points_and_review_frequency.ipynb` starts with one SKU
to show a built-in `(s,Q)` decision, its receipt, and its event evidence. It
then compares four review frequencies for 100 SKUs on a shared demand path,
including the `L+R` target window, the error from copying an `L`-only threshold,
an explicit operating-cost choice, and the built-in `(s,S)` sizing option.

Notebook `05c_extension_points.ipynb` starts with one dairy SKU, then extends
the same dated simulation to three. It shows a custom Monday/Thursday
`DecisionSchedule` and its changing target windows, a custom
`OrderingConstraint` for whole cases within chiller space, and the built-in
`ScheduledOrderHold` and `ScheduledInventoryAdjustment` callbacks. Cumulative
runs and their event/audit records show what each addition changes.

Notebook `05d_open_orders_and_suppliers.ipynb` follows a café chain buying
coffee beans. It starts with the familiar per-SKU run and the local roaster,
reads the same run order by order, and writes it again with the order-level
API with identical results. It then places one day by hand with two suppliers,
adds an importer with fixed and then seeded random lead times (deliveries that
overtake each other), compares planning lead times on shared draws, and ends
with dual sourcing, partial deliveries and a custom allocation rule. Purchase
costs per supplier come from the order frame at declared prices.

Notebook `06_fair_forecast_and_policy_comparisons.ipynb` compares direct
cumulative Smooth upper-quantile targets and two built-in inventory policies
on the same five-SKU demand replay. It separates ETS-model and quantile-level
changes, checks the first protection-window totals, then crosses ANN/AAN 95%
targets with `(R,S)` and `(s,Q)`. A declared per-SKU service floor and cost
screen show how to read an operating choice without treating one replay as a
general winner.
