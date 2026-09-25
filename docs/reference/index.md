# API reference

Every public class and function, generated from the docstrings in the source
code. For explanations and worked examples, start from the
[Guide](../user-guide/index.md); come here for exact signatures,
parameters, and return values.

## Where things live

Import each name from its home module:

| Module | Contents |
|---|---|
| `stockcast.core` | state, orders, the engine, schedules, constraints, callbacks, suppliers, processes |
| `stockcast.policies` | built-in policies and periodic-review target providers |
| `stockcast.evaluation` | the evaluator, ledger validation, metrics |
| `stockcast.utils` | demand generation and manual-loop primitives |
| `stockcast.visualization` | plots |

The most common names are also re-exported from the top-level `stockcast`
package.

## Pages

| Page | Objects |
|---|---|
| [Simulation engine](engine.md) | `SimulationEngine`, `SimulationResult`, `ComparisonResult` |
| [State and orders](state.md) | `InventoryStateDataFrame`, `OrderDecision`, `OrderLines` |
| [Policies](policies.md) | `BasePolicy`, `OrderUpToPolicy`, `ReorderPointPolicy`, `PeriodicReviewPolicy`, `SingleOrderPolicy`, `newsvendor_critical_fractile`, target providers |
| [Decision schedules](schedules.md) | `DecisionSchedule`, `PeriodicSchedule`, `OneTimeSchedule`, `ExplicitSchedule` |
| [Ordering constraints](constraints.md) | `OrderingConstraints`, `OrderingConstraint`, `MinimumOrderQuantity`, `OrderMultiple`, `MaximumOrderQuantity`, `ShelfSpaceLimit`, `ConstraintContext`, `ConstraintResult` |
| [Callbacks](callbacks.md) | `SimulationCallback`, `CallbackContext`, adjustment results, scheduled callbacks, `CallbackError` |
| [Suppliers](supply.md) | `SupplyModel`, `Supplier`, `SupplierAllocation`, `SupplierShares`, `AllocationContext`, `DeliveryOutcome`, `DeliveryContext` |
| [Processes and shelf life](processes.md) | `ShelfLifeEngine`, `ShelfLife`, `InventoryProcess`, `Flow`, `ProcessFlows`, `ProcessContext`, `StockChange`, `FIFOLotLedger` |
| [Evaluation](evaluation.md) | `InventoryEvaluator`, `validate_event_frame`, `BaseInventoryMetric`, `CoverageMetric` |
| [Metrics](metrics.md) | all 37 metric functions |
| [Utilities](utils.md) | `DemandGenerator`, `update_inventory_with_orders`, `place_order_lines`, `process_demand` |
| [Plots](visualization.md) | the six plotting functions |
| [Output tables](schemas.md) | columns of the ledger, order frame, callback audit, process flows, and manifest |

## Stability

For the 0.1.x releases, public imports, required arguments, output columns,
metrics, and documented behaviour keep their names and meanings. New
arguments, columns, and objects may be added. Names starting with an
underscore and direct imports from implementation modules are internal.
