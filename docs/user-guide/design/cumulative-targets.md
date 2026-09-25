# Targets cover a whole window

*A deep dive from our [Philosophy](../../get-started/philosophy.md).*

**Decision.** A forecast target describes total demand over the decision's
protection window: $S = Q_\alpha\left(\sum_{h=0}^{H-1} D_{t+h}\right)$. Stockcast
accepts the target of the window directly, or computes it from means and
standard deviations under a stated independence assumption. It never adds up
per-period quantiles.

## Why

**It is the question the decision asks.** An order today has to carry the shop
until the next order arrives. What matters is whether *total* demand over
that window exceeds the stock. A daily quantile answers a different question.

**Quantiles do not add.** The quantile of a sum equals the sum of quantiles
only when every period moves in lockstep. For ordinary demand, high and low
days partly cancel, so the sum of daily 95% quantiles is far above the 95%
quantile of the total: 60 against 46 packs in the
[tea-shop example](../../learn/03-forecast-targets.md). Adding daily
quantiles would quietly hold much more stock than the stated probability
implies.

**Dependence is part of the forecast.** Forecast errors are often correlated
across horizons. Sample paths and cumulative forecasts carry that dependence;
the variance of the total includes the covariance terms
([Forecast targets](../forecast-targets.md#from-means-standard-deviations-and-correlations)).
Asking for the window's target lets your forecasting model, which knows its
own dependence structure, do that part.

**The window is checked.** A target carries its horizon, origin, and end date.
Stockcast checks them against the policy's lead time and schedule, so a
target built for a two-week window is never used for a one-week decision by
mistake.

## What it means for you

- From sample paths: sum each path over the window, then take the quantile.
- From a model with cumulative forecasts: ask for the cumulative quantile.
- From means and standard deviations: use the independent-normal mode if
  independence is reasonable; otherwise include the covariances and pass the
  result as a direct target.
- Any forecasting library works, because all it needs to produce is one number
  per SKU and window.
