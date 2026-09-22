# 80 — Tests and evidence

## 80.1 Current executable evidence

The unit directory now contains 12 client-independent files. The release audit
added 12 delivery-calendar reference cases and two artifact-check regressions.
The original 155 cases plus the 12 reference cases passed against source and
an installed wheel on Python 3.12.13, with NumPy 2.5.3, pandas 3.0.6, and
Matplotlib 3.11.2. The same 167 cases passed against an installed wheel on
Python 3.10.12 with NumPy 1.23.0, pandas 1.5.0, and Matplotlib 3.6.0.

Current release-audit commands and final counts are recorded in
`RELEASE_READINESS.md`; do not infer publication or complete platform coverage
from local checks. The frozen public API is recorded in knowledge 93.

## 80.2 Test-file map

| File | Top-level test functions | Main evidence |
|---|---:|---|
| `test_callbacks.py` | 31 | lifecycle timing, built-ins/custom callbacks, validation failures, defensive inputs/outputs, audit, manifests, preflight/repeated/comparison reset, inventory-sensitive prediction, FIFO integration, fixtures, executable examples |
| `test_data_structures.py` | 23 | IDs, initialization, readiness, receipts, backlog/lost sales, demand and order timing |
| `test_demand_generator.py` | 4 | demand source shapes, negative handling, reproducibility/provenance |
| `test_inventory_evaluation.py` | 9 | canonical validation, metric surface, evaluator grouping/window choices, metrics and costs |
| `test_order_constraints.py` | 10 | built-in rules, adjustment/raise modes, order dependence, audit and reset |
| `test_policy_target_contracts.py` | 19 | direct targets, horizons, probabilities, dates, continuous-review timing, quantile guardrails, providers |
| `test_shelf_life.py` | 11 | lot balance, FIFO expiry/consumption, opening-lot modes, event integration |
| `test_simulation_contracts.py` | 21 | preflight, demand grid, events, schedules, comparisons, provenance |
| `test_extension_contracts.py` | 2 | removed `after_step` surface and absence of an active `pystate` package |
| `test_public_api_contract.py` | 2 | frozen namespace exports and version |
| `test_inventory_reference_model.py` | 1 | 12 independent delivery-calendar cases covering lead time, review period, shortage mode, opening orders, settlement, and costs |
| `test_release_artifacts.py` | 2 | rejection of foreign distribution metadata and missing artifact types |

The M5 and SPAR adapter-contract files remain in the private source repository
and are intentionally absent from the staging test tree.

Counts describe the current files and can drift after changes; re-run collection
instead of copying these numbers into release claims.

## 80.3 What the passing suite does not prove

- that `stockcast` is currently available on package registries or collision-free
  in every supported environment;
- that metadata, Apache notices, classifiers, dependencies, or Python versions
  are correct;
- that a source distribution and wheel contain only approved files;
- that installed-wheel provenance is populated;
- that public tutorials and API examples work;
- that all hooks preserve the event/state contract;
- that adapter tests can run after SPAR/M5 code is excluded;
- that results match an external benchmark or real operational system;
- production scale or performance, continuous-time or multi-echelon behavior,
  arbitrary forecasting-model compatibility, or automatic target calibration.
- supplier-specific order decisions through `SimulationEngine`; 0.1.0 places
  one composed decision per enabled decision opportunity, while the low-level
  state primitive retains repeated-order accumulation.

## 80.4 Extraction test strategy

When package construction is authorized, create evidence in layers:

1. copy/adapt only approved reusable modules and core tests (completed in the
   staging source tree);
2. rename imports deliberately and add import-surface tests (completed for the
   source tree);
3. separate core contract tests from private adapter tests (completed in the
   staging test tree);
4. test the supported Python/dependency matrix in clean environments;
5. build sdist and wheel;
6. inspect both archives for forbidden legacy/business material;
7. install each artifact into a clean environment and run public smoke examples;
8. verify version, license, and embedded source provenance from the installed
   artifact;
9. run scientific regression fixtures with immutable expected event ledgers.

Package builds and isolated installs were exercised in the September 2026
release audit. Publication remains a separate owner action.

## 80.5 Removed live-state hooks and typed callback evidence

The copied engine formerly validated and appended a period event before calling
`after_step`. A custom hook could therefore mutate returned inventory after the
canonical event was recorded. A reproduced case produced event
`ending_on_hand = 0.0` while returned final inventory had `on_hand = 100.0`.

The owner decided not to expose that unrestricted hook in Stockcast 0.1.0. The
staging implementation removes both the invocation and method. A focused
staging test asserts that `SimulationEngine` no longer exposes `after_step`.
Before the namespace migration, the focused test plus all 108
client-independent legacy cases passed together (`109 passed` on 2026-08-18).
After migration, those 109 cases plus the new retired-namespace boundary test
pass locally (`110 passed` on 2026-08-18).

The initial removal did not close every equivalent path. On 2026-08-19, a focused probe
overrode `after_period_event`, directly added 100 units to its live inventory
argument, and returned the already-built event unchanged. Validation passed;
the terminal event recorded `ending_on_hand = 5.0` while returned final
inventory had `on_hand = 105.0`.

The owner subsequently approved the two-phase typed callback contract. The
implementation removes all public hooks that received live state and preserves
shelf-life behavior through private engine phases. Callback effects are applied
by the engine before event validation. The callback suite covers no-callback
parity, ordering, failure, provenance, comparison reset, executable examples,
and event/state/FIFO continuity. Owner review remains required before the
release gate is closed.

## 80.6 Evidence commands

From the repository root, the configured suite can be checked without creating
a pytest cache:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  /home/filtheo/inventory/.venv/bin/python -m pytest \
  -p no:cacheprovider -q -o addopts='' tests/unit
```

Collect-only accounting for the local staging tests:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  /home/filtheo/inventory/.venv/bin/python -m pytest \
  -p no:cacheprovider -q -o addopts='' --collect-only tests/unit
```

Use a clean installed environment later for package evidence; passing against a
source path can hide missing build files and incorrect distribution metadata.

## 80.7 Documentation coverage audit

This knowledge layer is designed to be mechanically scannable:

- [70](70_module_reference.md) names all 23 candidate source modules;
- this page names all 9 staging unit-test files;
- [20](20_data_and_time_contracts.md) records state/order/demand contracts;
- [30](30_execution_flow.md) records the complete run sequence;
- [40](40_policies_and_targets.md) records all policy families and target modes;
- [50](50_constraints_and_shelf_life.md) records all built-in constraints and
  perishability;
- [60](60_events_evaluation_and_outputs.md) records event and evaluation flow.

Coverage means the area is mapped, not that every implementation line has been
restated. Agents should follow the source links before edits.
