# Policies API

Policies request orders from supplied state and fitted target information. They
never mutate engine-owned inventory. Choose `OrderUpToPolicy` for `(R,S)`,
`ContinuousReviewPolicy` for discrete-period `(s,Q)` or `(s,S)`, and
`PeriodicReviewPolicy` for `(R,s,S)`. See [forecast targets](../concepts/forecast-targets.md)
before fitting a forecast-derived policy.

::: stockcast.policies
    options:
      show_root_heading: false
      members: true
