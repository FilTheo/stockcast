# Forecast targets

Pyforia consumes forecast-derived inventory information; it does not fit a
forecasting model or manufacture uncertainty. The policy validates declared
target metadata and numerical inputs. It cannot establish whether an external
forecast is calibrated or whether its declared assumptions are true.

## Direct cumulative targets

For `OrderUpToPolicy`, a supplied target represents cumulative demand over the
protection horizon, normally `lead_time + review_period`. A direct target frame
records the target probability, forecast origin, frequency, horizon, end date,
and source. These fields must agree with the fitted policy and simulation.

```python
policy.fit(
    targets,
    target_column="target",
    target_probability=0.95,
    protection_horizon=lead_time + review_period,
    target_source="external_direct",
    forecast_origin=decision_date,
    forecast_frequency="D",
    target_end_date_column="target_end_date",
)
```

Do not pass marginal daily quantiles as a cumulative target and do not add them
together. A point forecast without the required target representation and
provenance is not a complete protection target.

## Supported independent-normal route

When independent normal per-step forecast errors are an appropriate declared
assumption, `OrderUpToPolicy.fit(...)` can aggregate supplied means and standard
deviations with `aggregation_method="independent_normal"`. Every required
standard deviation must be present; Pyforia does not substitute a heuristic.

For dependent forecast errors, cumulative variance also includes cross-horizon
covariances. Supply a direct cumulative target from a joint forecast model or
joint sample paths when independence is unsuitable. Do not pass marginal
standard deviations through the independent-normal route in that case.

## Target probability and realized service

`service_level=0.95` declares the target quantile probability. It does not
guarantee a 95% realized fill rate or cycle service level. Forecast calibration,
discrete review timing, opening stock, lost sales, expiry, and ordering
constraints affect realized service. Measure those outcomes from the event
ledger on held-out demand.

The built-in `(R,S)` target uses `lead_time + review_period` under the frozen
0.1 contract. `ContinuousReviewPolicy` is evaluated once per simulated period;
it is not a continuous-time simulator or an optimal lost-sales policy solver.

External targets are declarations: a caller could sum marginal quantiles
before passing a single number, and Pyforia cannot detect that mistake from
the number alone. Preserve the upstream calculation and training cutoff.

## Later forecast snapshots

Use `policy_schedule` to supply fitted policy snapshots at later eligible
decision periods. A snapshot can update targets but cannot change the policy
class, timing configuration, service level, or shortage mode. Its forecast
origin must match its decision date. See Notebook 04b and the [forecast
integration guide](../guides/forecast-integration.md).
