# 80 — Tests and evidence

Order-level pipeline and suppliers (2026-09-24,
[97](97_open_orders_and_suppliers.md)): additive; the per-SKU contract is
unchanged. Evidence for "unchanged" came from a local scratch harness (not a
repository test). It recorded every outermost engine run's event ledger,
history, final state, callback audit, run settings, manifest (minus run id,
timestamp and source commit), summary, and every state and order frame handed
to policies. It compared them with a frozen copy of the pre-change source:

- all 307 engine runs of `tests/unit` + `tests/stress` identical under pandas
  2.3.3 and 3.0.6;
- all 88 engine runs of the 18 existing notebooks identical under pandas 3.0.6,
  with seeded random draws as for the NumPy-kernel check;
- the same comparison with every run rerouted through
  `supply=SupplyModel([Supplier(..., lead_time=policy.lead_time)])` also gave
  307/307 and 88/88 identical runs (only `run_settings["supply"]` added). This
  is the exact mapping of the default engine onto the order-level API.

Permanent tests are in `tests/unit/test_open_orders_and_supply.py` (80 cases):

- attribution of `in_transit`; declared open orders and their fail-closed
  validation; rejection of edits after declaring;
- manual `place_order_lines` versus `update_inventory_with_orders`
  equivalence for `L=0,1,3`;
- engine mapping equivalence across lead time, shortage mode, review period,
  constraints, callbacks and shelf life;
- two-supplier ledger identities and order-frame reconciliation; seeded
  random lead times and overtaking; partial deliveries;
- shared draws across comparison branches; array versus forced-DataFrame path
  equality; shelf-life lots;
- custom allocations and their validation; configuration errors;
- a policy editing its own state copy behaves as before.

`test_public_api_contract.py` locks the appended exports and
`ORDER_FRAME_COLUMNS`. Final source:

- `env PYTHONPATH=src python -m pytest -q -o addopts='' tests/unit tests/stress`
  passed 396 tests under Python 3.12.13 / pandas 3.0.6 and under Python
  3.10.12 / pandas 2.3.3;
- the 307-run comparison was repeated on this final source.

The 88-run notebook comparison was made before the last internal edits, which
touched only the order book (object-array fast path, shared book copies,
vectorized order-frame dates, and the `checked` flag) and not the accounting.
Rerun the notebooks before release.

Performance is not fully measured. Profiling a 1000-SKU daily order-up-to run
showed about 1.4% more Python function calls than before; wall-clock
benchmarks were inconclusive because the machine was shared with other
workloads. Rerun `tests/benchmark/engine_benchmark.py` on an idle machine
before quoting timings. Notebook 05d's explanation pass has 52 cells: it
separates the cumulative-target, policy-fit and shared-run helpers, and adds
timing, order-reconciliation, paired-supplier-draw and cost-window guidance.
It executed from source with no cell errors or stderr; the two figures were
inspected, and saved outputs were copied after source-cell equality and
notebook validation. Scenario calculations were not changed.

NumPy period kernel (2026-09-24): `SimulationEngine` now keeps live state as
arrays and assembles history and the event ledger once
([30.10](30_execution_flow.md#3010-internal-state-representation)). Public
API and outputs are unchanged. Evidence against a frozen copy of the previous
engine, comparing event frames, history, final state, callback audit, run
settings, manifests and every DataFrame handed to policies, callbacks and
constraints (values, dtypes, column order, index, pipeline arrays): all 233
engine runs in `tests/unit` + `tests/stress` identical under pandas 2.3.3 and
3.0.6, and all 86 engine runs of the 18 notebooks identical under pandas 3.0.6
from a frozen notebook snapshot with seeded random draws. Seeding was needed
because notebooks 04e and 09 build targets from unseeded `smooth` Monte Carlo
intervals; the previous engine also differs from itself on those runs. The
comparison harness was local scratch tooling, not a repository test. The
permanent guard is `tests/unit/test_array_kernel_equivalence.py`, which forces
the DataFrame period path and requires exact equality (or the identical
exception). `tests/benchmark/engine_benchmark.py` measured, on a 12-core WSL2
machine with pandas 2.3.3, 10 / 1000 SKUs, ms per daily period, previous →
new: weekly `(R,S)` 74 → 2.4 / 179 → 8.0; daily `(R,S)` 113 → 14.6 /
211 → 31; constraints plus a physical callback 62 → 12 / 166 → 47; FIFO
shelf life with daily review 98 → 17 / 235 → 48. Remaining decision-period
cost is mostly the policy's own `predict`, which was deliberately not changed.

Notebook 05b was expanded on 2026-09-24 from a compact review-frequency
comparison into a one-SKU-to-100-SKU tutorial. It checks a daily `(s,Q)`
decision and two-period receipt in validated events, then compares daily,
12-hour, 6-hour, and 3-hour review on the same three-hour demand buckets with
the same physical lead time, fixed `Q`, and fixed opening stock. It contrasts a
correct `L+R` threshold with an `L`-only planner threshold, distinguishes
cycle service from fill rate, and evaluates ordering, holding, and backlog
costs after converting per-day rates to each step length. An external
review-check charge is separately labelled; a final `(s,S)` run checks the
other built-in sizing mode. Source-import nbclient execution of
`05b_reorder_points_and_review_frequency.ipynb` passed with 55 cells, six
inspected figures, no cell stderr or error outputs, and source-matched saved
outputs. The explanation pass split the regime setup and cost evaluation into
short cells, retaining the same 12-hour choice under the declared rates.
Focused tests passed with `env PYTHONPATH=src python3 -m pytest -q
tests/unit/test_decision_timing.py tests/unit/test_policy_target_contracts.py
tests/unit/test_inventory_evaluation.py` (65 passed). Installed-artifact
notebook execution remains a separate release check; no core code changed.

Notebook 05c was expanded on 2026-09-24 into a one-SKU-to-three-SKU
extension tutorial. Its 44 cells separately demonstrate the irregular schedule
and dated targets, a custom whole-case chiller constraint, a supplier-order
hold, and a signed stock-count adjustment. Source-import nbclient execution
passed. The three rendered figures were inspected, the
event/callback tables and four-run scoring comparison were checked, and 109
focused timing, constraint, callback, and simulation tests passed with
`env PYTHONPATH=src python3 -m pytest -q tests/unit/test_decision_timing.py
tests/unit/test_order_constraints.py tests/unit/test_callbacks.py
tests/unit/test_simulation_contracts.py`. The saved notebook outputs were
copied only after source-cell equality and notebook validation.

Notebook 06 was revised on 2026-09-24 to compare direct four-day cumulative
upper-quantile targets across ETS configuration and probability, then hold the
ANN-95 target fixed while comparing built-in `(R,S)` and `(s,Q)` policies. The
final ANN/AAN 95% by-policy matrix uses the same `L+R` target horizon for both
rules and checks identical `(s,Q)` orders despite different target values. A
one-origin cumulative-demand/pinball diagnostic is labelled as descriptive.
The retrospective operating screen requires at least 95% scored fill for each
SKU and selects ANN-95 `(R,S)` as the lowest-cost eligible branch (163.832
under declared rates); both `(s,Q)` branches have worst-SKU fill 0.786. The
source-import notebook run passed with 60 cells, seven inspected figures, no
cell stderr or errors, and source-matched saved outputs. Focused tests passed
with `env PYTHONPATH=src python3 -m pytest -q
tests/unit/test_policy_target_contracts.py tests/unit/test_inventory_evaluation.py
tests/unit/test_simulation_contracts.py` (54 passed). No core code changed.

Notebook 05 was revised on 2026-09-24 to name its starter rule as the familiar
`(s,Q)` policy, compare declared `Q` candidates on calibration demand using
explicit ordering, holding, and shortage costs, and check the selected rule and
a shortfall-responsive subclass on separate held-out demand. Source-import
nbclient execution of `05_custom_policies.ipynb` passed with four inspected
figures, no cell stderr, a changed-order evidence table, and source-matched
saved outputs. The annotated revision reran successfully and asserts that the
two held-out event ledgers align by SKU and date before comparing orders.
Focused policy-target and evaluation tests passed with
`env PYTHONPATH=src python3 -m pytest -q
tests/unit/test_policy_target_contracts.py tests/unit/test_inventory_evaluation.py`
(32 passed). This is notebook and documentation work; no core code changed.
Installed-artifact notebook execution remains a separate release check.

The 2026-09-24 Notebook 04 tutorial revision now has six lessons: weekly
one-period forecasts through both order-up-to and single-order APIs, daily
newsvendor economics, a fixed cumulative target, rolling cumulative targets,
the cumulative-method comparison, and a scheduled forecast-to-simulation
capstone. Source-import nbclient execution of all six
passed with no cell stderr or error outputs using
`/tmp/stockcast-timing-validation/bin/python /tmp/run_stockcast_source_notebooks.py '04*.ipynb'`.
All 17 rendered figures were inspected; executed outputs were saved after
source-cell equality checks. The weekly notebook asserts matching order,
receipt, sales, stock, and lost-sales flows for its two APIs. The daily
newsvendor asserts the inverse-CDF and expected-profit choice and validates
its one-order accounting. Notebook 04f checks irregular decision periods,
forecast cutoffs and coverage windows, scheduled policy snapshots, one-period
receipts, and first-period physical and lost-sales accounting. Focused timing
and target tests passed with `env PYTHONPATH=src python3 -m pytest -q
tests/unit/test_decision_timing.py tests/unit/test_policy_target_contracts.py`
(56 passed). The earlier focused timing, target, and evaluation tests passed
with `env PYTHONPATH=src python3 -m pytest -q
tests/unit/test_decision_timing.py tests/unit/test_policy_target_contracts.py
tests/unit/test_inventory_evaluation.py` (65 passed). Installed-artifact
notebook execution is still a separate release check; no core code changed.

Notebook 02b (2026-09-24) executes five decision schedules on the same explicit
inventory scenario and asserts complete event-frame equality between
`review_period=3` and `PeriodicSchedule(every=3)`. Its rendered timeline was
inspected; source-matched executed outputs are saved in the notebook. This
addition changes examples/documentation only, not engine behavior.

Pre-release stress suite (2026-09-24): `tests/stress/test_prerelease_stress.py`
(77 cases, run separately from `tests/unit`) compares 36 randomized
multi-SKU configurations against an independent calendar oracle. Configurations
cover OUT, `ReorderPointPolicy` `(s,Q)`/`(s,S)` in quantile and planner
modes, `(R,s,S)` and custom policies, periodic/explicit
schedules, `L=0..4`, both shortage modes, opening pipeline/backlog,
constraints, FIFO shelf life, and experiment windows. It also checks
cross-period continuity, delivery tracing, caller-input isolation, causality
under demand shocks, and the exact `L+R` net-stock identity with normal-quantile
calibration. Further checks: the lead-time and irregular-window `(u-t)+L`
identities for any policy, the reorder-point `L+1` window, repeated
and single-season newsvendor economics, a rolling weekly perishable retailer
workflow with callbacks, evaluator costs and a shelf-life comparison, and
fail-closed production inputs. The suite found and fixed two defects:
`ShelfLifeEngine.run_comparison` and the fixed balance tolerance (knowledge 50.6).
It also drove the owner-approved replacement of `ContinuousReviewPolicy` with
`ReorderPointPolicy` (knowledge 40.5).

## 80.1 Current executable evidence

Current timing-migration source validation: 200 unit tests passed; see knowledge
95 for the exact command and environment. The public engine-design page maps
its claims to the independent timing/reference oracles and canonical ledger
checks. Counts and installed-artifact results in the paragraph below are the
**historical pre-migration release baseline**, not current release evidence.

At that baseline, the unit directory contained 12 client-independent files. The release audit
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
| `test_policy_target_contracts.py` | 20 | direct targets, horizons, probabilities, dates, reorder-point review timing and planner mode, quantile guardrails, providers |
| `test_shelf_life.py` | 13 | lot balance, FIFO expiry/consumption, opening-lot modes, event integration |
| `test_simulation_contracts.py` | 21 | preflight, demand grid, events, schedules, comparisons, provenance |
| `test_extension_contracts.py` | 2 | removed `after_step` surface and absence of an active `pystate` package |
| `test_public_api_contract.py` | 2 | frozen namespace exports and version |
| `test_inventory_reference_model.py` | 1 | 12 independent delivery-calendar cases covering lead time, review period, shortage mode, opening orders, settlement, and costs |
| `test_release_artifacts.py` | 2 | rejection of foreign distribution metadata and missing artifact types |
| `test_open_orders_and_supply.py` | 30 (80 cases) | open-order attribution and declaration, `OrderLines`/`place_order_lines`, exact default-to-`SupplyModel` mapping, suppliers, random lead times, partial deliveries, order frame reconciliation, comparison draws, array/DataFrame equality, shelf life, allocation validation |
| `test_array_kernel_equivalence.py` | 10 (36 cases) | array kernel versus forced DataFrame period path: randomized schedules, lead times, shortage modes, windows, constraints and callbacks; shelf life; non-canonical opening state; integer orders/targets/SKUs; custom SKU column with zero lead time; history-reading policy; rolling policy schedule; comparisons; identical errors; path usage |
| `../stress/test_prerelease_stress.py` | 13 (77 cases) | randomized independent oracle, ledger invariants, causality, `L+R` identity and calibration, newsvendor economics, retailer workflow, fail-closed inputs |

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
- supplier behaviour beyond the declared `SupplyModel`: delays after
  placement, supplier capacity or MOQ, supplier-level costs in metrics, and
  multi-echelon networks (knowledge 97.5).

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

Engine wall-clock benchmark (not an acceptance gate; machine dependent):

```bash
env PYTHONPATH=src MPLCONFIGDIR=/tmp/stockcast-mpl \
  .venv/bin/python tests/benchmark/engine_benchmark.py --skus 10 100 1000 --periods 365
```

Add `--seeds N --workers W` to time independent seeds in a process pool.

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

## Before-demand timing migration

The earlier counts above are historical. `test_decision_timing.py` adds independent
calendar oracles for zero and positive lead times, nonperiodic target windows,
single-season demand, and FIFO/backlog accounting. Existing tests now exercise
the approved before-demand contract; see knowledge 95.
