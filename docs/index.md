![Stockcast logo](https://raw.githubusercontent.com/FilTheo/stockcast/main/stockcast_logo.png){ width="180" }

# Stockcast

> Turn forecast-derived inputs into inspectable inventory decisions.

Stockcast is a Python library for researchers, data scientists, and practitioners
who need to connect a forecasting workflow to replenishment decisions. Supply
explicit forecast-derived targets and inventory assumptions, simulate the
resulting decisions, and evaluate operational outcomes from a validated event
ledger.

Stockcast is not a forecasting trainer, ERP, procurement system, or black-box
optimizer. Your forecasting workflow remains responsible for producing
forecast information; Stockcast owns the inventory decision, simulation, and
evaluation layer that follows.

```text
forecasting code
    -> forecast-derived target
    -> policy and order decision
    -> inventory simulation
    -> validated event ledger
    -> operational evaluation
```

## Start here

- [Install Stockcast](installation.md), then run the [first simulation](tutorials/first-simulation.md).
- Use [forecast integration with smooth](guides/forecast-integration.md) to
  connect an external forecast to a dated inventory target.
- Explore the [notebook collection](tutorials/notebooks.md) for foundations,
  comparisons, shelf life, callbacks, and integration patterns.
- Consult the [API reference](reference/index.md) when you know the workflow
  you want to build.

## What you can do

- Map externally calculated cumulative forecast targets to replenishment
  decisions.
- Compare forecasts, policies, and explicit inventory assumptions through
  service, stock, shortage, and cost outcomes.
- Build a complete simulation with auditable state transitions and event rows.
- Extend a workflow with supported policies, target providers, constraints, and
  typed callbacks.
- Use Stockcast as one component in a larger Python decision pipeline.

## The 0.1 public contract

Stockcast 0.1 documents a frozen public import surface across `stockcast`,
`stockcast.core`, `stockcast.policies`, `stockcast.evaluation`, `stockcast.utils`, and
`stockcast.visualization`. The reference documents every exported object in
those namespaces. The durable outputs are the event ledger, callback audit
frame, run manifest, and compact summary; see [events, results, and
evaluation](concepts/events-and-evaluation.md).

The examples use explicit calendars, opening state, lead time, costs, and
forecast provenance. This is intentional: inventory results are meaningful only
when those assumptions are visible.
