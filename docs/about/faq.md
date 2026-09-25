# Frequently asked questions

## Getting started

**Does Stockcast make forecasts?**

No. Stockcast is the step after forecasting. Any forecasting library works:
you give Stockcast a target per SKU derived from your forecast (a quantile of
cumulative demand, sample paths, or means and standard deviations). See
[Connect any forecasting model](../how-to/connect-a-forecaster.md).

**Which forecasting library should I use?**

The one you already trust. The notebooks use
[smooth](https://openforecast.org/smooth-py/); statsforecast, skforecast,
sktime, Prophet, scikit-learn models, neural networks, and your own code work
the same way.

**Do I need to know inventory theory?**

No. [Learn the basics](../learn/index.md) introduces every idea with a small
example, and the [Guide](../user-guide/index.md) adds the theory when you
want it.

**Why does `SimulationEngine.run` have so many required arguments?**

Each one is a choice that changes results: how long the run is, which window
you score, what the period length is, which demand was used. Writing them
down makes every experiment explicit and reproducible. Once written, put them
in a dict and reuse it, as the examples do with `run_settings`.
[Every input is explicit](../user-guide/design/explicit-inputs.md) explains
the thinking.

## Modelling

**What does lead time mean exactly?**

An order placed in period $t$ is received at the start of period $t + L$,
before that period's demand. `lead_time=0` means it arrives in time for
today's customers. See [Timing](../user-guide/concepts/timing.md).

**Why is my order-up-to target for $L + R$ periods?**

Today's order is the last one that can arrive before the order placed at the
next review, which lands $R + L$ periods from now. The
[derivation](../user-guide/concepts/timing.md#the-protection-horizon) takes a
few lines.

**Can I add up my model's daily 95% quantiles to get the target?**

Use the quantile of the total instead: it answers the question the decision
asks and holds much less excess stock. [Targets cover a whole window](../user-guide/design/cumulative-targets.md)
shows why, with numbers.

**My target probability is 0.95 but the fill rate is 0.99 (or 0.90). Why?**

The target probability describes the forecast of demand over the window.
Realised service also depends on the opening stock, lost sales, order
constraints, supplier reliability, and how accurate the forecast is. Measuring
that gap is one of the most useful things a simulation does.

**Does Stockcast support continuous review?**

Stockcast simulates discrete periods and reviews at period boundaries. A
reorder-point policy checked every period (`review_period=1`) is the discrete
counterpart; shorter periods (hours instead of days) bring it closer to
continuous review. See [Reorder point](../user-guide/policies/reorder-point.md).

**Can lead times be random?**

Yes, with a [supply model](../user-guide/suppliers.md): each supplier can have a
fixed or random lead time, partial deliveries, and
[unreliable deliveries](../user-guide/unreliable-deliveries.md).

**Can I model perishable products?**

Yes: FIFO shelf life with `ShelfLifeEngine` or the `ShelfLife` process, and
any other physical flow with your own
[inventory process](../user-guide/processes.md).

**Multi-echelon networks?**

Stockcast 0.1 models one stocking point with any number of SKUs and suppliers.
`SupplierAllocation` and the order frame are natural starting points for
network extensions.

## Using results

**Where is the "truth" of a run?**

In the event ledger, `result.to_event_frame()`. Every metric and plot is
computed from it, and every row satisfies the stock-balance identities.

**How do I compare two forecasts fairly?**

Fit the same policy on each forecast's target and run them with
`run_comparison`, which shares the demand and copies the opening state for
each branch. See [Compare forecasts and policies](../how-to/compare-policies.md).

**Can I use Stockcast to place real orders?**

Yes: the same state, policy, and constraint objects compute today's order from
today's stock and forecast. See [From backtest to production](../learn/09-production.md)
and [Use Stockcast in a daily job](../how-to/production.md).
Your application stays in charge of data, approval, and sending orders.

## Project

**Is the API stable?**

For the 0.1.x releases, public imports, arguments, output columns, and
metrics keep their names and meanings. New features are added as optional
arguments and new objects.

**How do I cite Stockcast?**

See [Citation and support](../support.md).
