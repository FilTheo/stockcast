# Policies API

Policies request orders from supplied state and fitted target information. They
never mutate engine-owned inventory. Choose `OrderUpToPolicy` for `(R,S)`,
`ReorderPointPolicy` for `(s,Q)` or `(s,S)` under any decision schedule, and
`PeriodicReviewPolicy` for `(R,s,S)` targets from a provider. Review timing
comes from `review_period` or a `DecisionSchedule`; `review_period=1` gives
every-period review. See [forecast targets](../concepts/forecast-targets.md)
before fitting a forecast-derived policy.

::: stockcast.policies
    options:
      show_root_heading: false
      members: true
