> **Timing migration, 2026-09-23:** the owner approved a new default
> before-demand engine. Audit results below predate this semantic change and
> must not be used as release approval for it. Current source/notebook checks
> are recorded in `knowledge/95_decision_timing_implementation.md`; installed
> wheel/sdist and hosted CI release gates must be repeated before publication.
>
> **Source validation, 2026-09-24:** 200 unit tests and all 13 migrated
> notebooks passed; strict documentation and source-import smoke checks passed.
> See [the change and rationale report](ENGINE_TIMING_CHANGES.md). This does
> not replace the installed-artifact release audit below.

> **Pre-release stress audit, 2026-09-24 — current status.** Source-level
> evidence for the migrated engine:
>
> | Check | Result |
> |---|---|
> | `pytest tests/unit` | 205 passed |
> | `pytest tests/stress` | 77 passed (new pre-release suite) |
> | Notebooks against source | 16/16 passed, including the new 05b and 05c |
> | `mkdocs build --strict` | passed |
> | README smoke | fill rate 1.0, 35 units |
> | Ruff (fatal errors), `git diff --check` | clean |
>
> Changes from this audit:
>
> - fixed `ShelfLifeEngine.run_comparison`, which always raised `TypeError`;
> - made balance tolerances scale with flow size, so gram-scale runs no longer
>   abort;
> - replaced `ContinuousReviewPolicy` with `ReorderPointPolicy` (owner-approved;
>   see `docs/release-notes.md`).
>
> None of this is installed-artifact evidence.

## To do before publication

Blocking gates. Nothing below has been done for the current source.

- [ ] Rebuild the wheel and sdist from the release commit. Inspect both for
      excluded material: `knowledge/`, `chat_copy.md`, company/SPAR code,
      M5 data outside the documented subset.
- [ ] Install each artifact in clean environments: the minimum-dependency
      Python 3.10 and the latest supported Python. Against the *installed*
      package, run `tests/unit`, `tests/stress`, `tests/release/artifact_smoke.py`
      and `tests/release/run_notebooks.py`.
- [ ] Run the modified release workflows on hosted CI.
- [ ] Decide the repository and project URLs in `pyproject.toml` (still open).
- [ ] Owner review of the breaking `ContinuousReviewPolicy` removal and of the
      release notes, then tag and publish.

Recommended before 0.1, not blocking:

- [x] Extension-points notebook `05c_extension_points.ipynb`: a custom
      `DecisionSchedule`, a custom `OrderingConstraint` (whole case packs within
      shelf space), `OrderMultiple`/`ShelfSpaceLimit` conflict, `ScheduledOrderHold`,
      `ScheduledInventoryAdjustment`, `CoverageMetric`, and backorders. It passed
      source execution on 2026-09-24.
- [ ] Still no notebook for `ColumnPeriodicReviewTargets`, `ScheduledOrderOverride`
      or `ScheduledOrderMultiplier`; only 04d uses a non-daily calendar.

Known limitations to state as roadmap, not 0.1 promises:

- **No multi-echelon networks.** One stocking point per SKU, with an unlimited,
  reliable external supplier.
- **Supply and cost model:**
  - lead times are fixed; there are no stochastic lead times, supplier fill
    rates or multi-sourcing;
  - no shared per-order fixed cost (joint replenishment);
  - the SKU universe is fixed for a run.
- **Extension gaps:**
  - physical processes other than FIFO expiry (returns, supplier shortfalls,
    transfers) only via after-demand adjustment callbacks; lifecycle phases
    are private;
  - `OrderMultiple` and `ShelfSpaceLimit` cannot be composed when space binds;
    it needs a custom constraint.
- **Scale:**
  - about 0.13 s per simulated period regardless of SKU count;
  - FIFO lot-dust mismatches remain possible near 1e9 units per SKU-period.

# Stockcast 0.1.0 release-readiness audit

Audit date: 2026-09-22. Baseline: `a8252c2`, plus the local audit changes.
No package was published, no release/tag was created, and no changes were
pushed by this audit. The checkout changed during the audit; the current
twelve notebooks were executed again and their source hashes checked.

## Assessment

The core is a credible candidate for an initial **alpha** release as a
DataFrame-based inventory decision and simulation library. No numerical
discrepancy was found in the tested core contracts. That is evidence for the
declared discrete-period model, not a proof of universal scientific correctness
or operational suitability.

The design supports the intended modular workflow: external forecast -> fitted
policy -> inventory state/engine -> validated events -> evaluation. Its
PyTorch-like aspect is composable objects and subclassable policies/callbacks.
It does not provide tensors, automatic differentiation, GPU execution, or
end-to-end differentiable forecasting/inventory optimization.

The owner confirmed permission to retain the M5 example subset. Publication
should wait for verification of the final release commit in CI and publisher
account setup. Do not confuse successful notebook execution with acceptance of
all its scientific claims.

The local release-readiness audit and authorized corrections are complete.
The release sequence below is the publication handoff: final-commit hosted
checks, account configuration, TestPyPI validation, and publication have not
been performed by this audit. The confirmed M5 decision and removal of the
invalid Notebook 04c method close the two owner decisions raised during review.

## Verified evidence

| Check | Result |
|---|---|
| Source suite, Python 3.12.13 | 167 passed before the two packaging regressions were added |
| Installed-wheel suite, Python 3.12.13 | Final 169 passed in 35.40 seconds |
| Installed-wheel suite at declared dependency floors | 167 passed in 49.57 seconds; Python 3.10.12, NumPy 1.23.0, pandas 1.5.0, Matplotlib 3.6.0 |
| Independent scalar reference model | 12 cases passed: lost sales/backorders, L/R combinations, opening decisions, settlement and explicit costs |
| Packaging regressions | Both passed; contaminated archive rejected before correction |
| Wheel and sdist | Built successfully; Twine metadata checks passed |
| Artifact boundary | Final wheel: 28 members; sdist: 43; no examples/data/docs/archive or foreign egg metadata |
| Separate wheel/sdist installations | `pip check` and public-import/README smoke passed for each |
| Current examples | All 12 execute, including 04c; 44 figures reviewed |
| Documentation | Strict MkDocs build passed |
| Modified validation scripts | Ruff passed; `git diff --check` passed |

The Python 3.12 environment used NumPy 2.5.3, pandas 3.0.6, Matplotlib 3.11.2,
and Smooth 1.0.7. The 3.10 floor run emitted 22 dependency/test warnings, not
failures. Some notebooks emit pandas datetime-rounding warnings, and Notebook
02 emits a layout warning. Visual review found crowded dates/legends in some
figures; these are presentation limitations, not failed stock balances.

GitHub's release-check run
[35709225369](https://github.com/FilTheo/stockcast/actions/runs/35709225369)
was confirmed successful for commit `291d8ce`, including Python 3.10, 3.11,
3.12, 3.13 and the installed-artifact job. It predates the audit's final
workflow/manifest changes and does not validate those uncommitted changes.
Local validation covered Linux/Python 3.10 and 3.12; it is not a fresh local
Windows/macOS or Python 3.11/3.13 test run.

## User workflow and inventory science

In Notebook 04, an external ANN forecast produced a direct four-day upper
target of approximately 27.777 units at probability 0.95. With 12 observed
opening units, lead time 2 and review period 2, the first requested order was
15.777 units. It entered the pipeline, arrived on the second demand day, and
the scoring window had fill rate 1.0, average on-hand approximately 12.027,
and 51 ordered units. The forecast and holdout were chronologically separated.

The scalar reference model independently schedules receipts by delivery date;
it does not use Stockcast's pipeline-array implementation. Its period-by-period
receipts, fulfillment, shortages, old-backlog clearance, ending stock,
backlog, pipeline, orders, fill rate and cost matched engine results. Existing
tests additionally exercise FIFO/expiry, constraints, callbacks, target
validation, shared-demand comparisons, and output/API contracts.

Scientific interpretation still requires care:

- A cumulative target is a quantile of **total demand**, not a sum of daily
  quantiles. Joint simulation/bootstrap paths should be summed per path before
  taking a percentile. Dependence and training cutoffs remain caller duties.
- Independent-normal aggregation is conditional on the independence/normal
  assumptions. Correlated errors require covariance or joint samples.
- `service_level` specifies target probability; it is not a guarantee of
  realized fill rate, cycle service, or optimality under lost sales/constraints.
- The frozen `(R,S)` horizon is L+R. Receipt timing, end-of-period review, and
  the once-per-period implementation of continuous-review policies must remain
  explicit when comparing results with continuous-time theory.
- Fill rate measures immediate fulfillment. Later backlog clearance is
  separate. Pooled average-stock metrics average SKU-period rows; portfolio
  peaks/variances use period totals. System order-event counts should not be
  interpreted as per-SKU counts.
- Holding uses ending stock; purchase cost is charged at placement. Explicit
  evaluation windows and terminal treatment are needed for fair cost studies.
- M5 sales are treated as demand in these examples. Without availability data,
  that does not establish uncensored customer demand. Costs, lots, initial
  inventory and supplier conditions are hypothetical.

## Fixes made during this audit

1. Added the independent reference-model tests and installed-notebook runner.
2. Clarified probability, metrics, cost-window and external-target limitations
   in public documentation; added the joint-path target explanation.
3. Corrected five incompatible cell-ID fields in the format-4.4 Notebook 06.
   Its code and scientific calculations were unchanged.
4. Narrowed the source manifest: a local `src/stockcast.egg-info` directory had
   entered the sdist via `graft src`. The local directory was preserved;
   artifacts now exclude it. Added regression tests and a checker rejection.
5. Added publishing gates for artifact content, installed-wheel tests,
   source-install smoke and release-tag/version agreement. Added a dependency
   floor CI job. These workflows were inspected and their local commands
   exercised; hosted execution of the modified workflows remains pending.
6. Updated internal evidence coverage and marked the extraction guide's old
   no-build instructions as superseded by the current release audit.

## Remaining decisions and limitations

**04c resolved by owner decision:** the invalid construction was removed from
the notebook entirely, including its policy, plots, tables, counterexample
discussion, and stored outputs. The remaining methods are independent-normal
aggregation under explicit Gaussian/independence assumptions, external
approximate cumulative forecasting, and external simulated cumulative
forecasting. Assertions no longer require Monte Carlo agreement or coverage
of the single realized demand total. No core policy semantics changed.

The corrected notebook passed schema/compilation and clean installed-package
execution in 11.52 seconds; all three rendered figures were inspected. The
remaining two stderr messages are pandas datetime-rounding warnings. Focused
policy-target tests passed (22 tests), and the strict documentation build
passed in 6.02 seconds. Validation commands:

```bash
/tmp/stockcast-release-Jp8oV1/tools/bin/python tests/release/run_notebooks.py /tmp/stockcast-release-Jp8oV1/valid-04c-accepted --match '04c*'
/tmp/stockcast-release-Jp8oV1/tools/bin/python -I -m pytest -q -p no:cacheprovider tests/unit/test_policy_target_contracts.py
/tmp/stockcast-release-Jp8oV1/tools/bin/python -m mkdocs build --strict --site-dir /tmp/stockcast-release-Jp8oV1/valid-04c-site
```

The updated notebook SHA-256 is
`1757e58953eeb2cb6bd6c5467c906dddc2b74fb38d4925dd97a5017da66de6a8`.
Its execution report supersedes the earlier 04c report; the other eleven
notebooks' earlier evidence remains unchanged.

**Monte Carlo reproducibility:** 04c and 09 use external unseeded Smooth
simulation. Their numeric targets, and potentially the selected method in 09,
can change between runs. They are not deterministic acceptance benchmarks.
Keep the seed limitation explicit, preserve target samples/catalogues, and use
held-out calibration plus adequate simulation precision before operational
claims. Notebook 09 is Stage 1 model selection; its fit/forecast/accounting
assertions pass, but Stage 2 final-test evidence is still outside its scope.
Notebook 10 is an application-owned daily-close demonstration, not evidence of
a deployed transactional ordering service.

**M5 decision resolved:** on 2026-09-22 the owner confirmed having permission
and explicitly instructed that the existing subset remain in the repository.
This records the owner's confirmation, not an independently verified public
license grant. Existing attribution and exclusion from the Apache code license
and PyPI artifacts remain in effect. The owner plans to add the preparation
script later; that follow-up is not a blocker for this audit. No data files
were removed or re-licensed.

**Release identity and account setup:** verify control of the PyPI/TestPyPI
project names and configure the matching trusted publishers and GitHub
environments. A public project-page lookup is not proof a name is registrable.
No credentials or account configuration were requested or changed.

## Release sequence

1. Retain the approved M5 subset and its attribution. Re-execute any further
   changed examples and retain their outputs/source hashes. Keep the release
   alpha claim and stated scientific limitations explicit.
2. Review and commit an exact allowlist of the release changes. Confirm that
   package identity, version `0.1.0`, license, repository/documentation URLs,
   README rendering, and the intended public tree are correct.
3. Run the final commit through the Python 3.10–3.13 CI matrix, dependency-floor
   job, strict docs, wheel/sdist checks, clean installs, and notebook runner.
   Archive build hashes and results against that commit.
4. Configure PyPI and TestPyPI trusted publishers for this repository's
   `publish.yml` workflow, with matching `pypi` and `testpypi` environments.
   Use GitHub environment approval protection for intentional publication.
   [PyPI trusted-publisher instructions](https://docs.pypi.org/trusted-publishers/).
5. With explicit owner authorization, dispatch the TestPyPI workflow, install
   the candidate into a clean environment, and repeat the smoke. Obtain normal
   dependencies from PyPI separately, then install the TestPyPI candidate with
   `--no-deps` to keep the indexes unambiguous.
6. After validation and owner approval, create the matching `v0.1.0` tag and
   GitHub release. The current workflow publishes on `release: published`;
   publishing that GitHub release is a consequential action, not a draft step.
7. Install `stockcast==0.1.0` from PyPI in a fresh environment, run the smoke,
   verify documentation links and retain the final uploaded artifact hashes.
   [Python packaging guide](https://packaging.python.org/en/latest/tutorials/packaging-projects/).

## Reproduction and retained local evidence

The disposable audit root is `/tmp/stockcast-release-Jp8oV1`. It contains tools,
isolated install environments, source/wheel/sdist builds, rendered docs, and
executed notebook copies. Temporary storage is not a permanent release record.

Commands used (from the repository unless a working directory is shown):

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src /tmp/stockcast-release-Jp8oV1/tools/bin/python -m pytest -p no:cacheprovider -q -o addopts='' tests/unit
# Installed package, cwd=/tmp/stockcast-release-Jp8oV1:
/tmp/stockcast-release-Jp8oV1/tools/bin/python -I -m pytest -p no:cacheprovider -q -o addopts='' /home/filtheo/stockcast/tests/unit
/tmp/stockcast-release-Jp8oV1/floor-env/bin/python -I -m pytest -p no:cacheprovider -q -o addopts='' /home/filtheo/stockcast/tests/unit
/tmp/stockcast-release-Jp8oV1/tools/bin/python -m build --outdir /tmp/stockcast-release-Jp8oV1/final-dist
/tmp/stockcast-release-Jp8oV1/tools/bin/python -m twine check /tmp/stockcast-release-Jp8oV1/final-dist/*
/tmp/stockcast-release-Jp8oV1/tools/bin/python tests/release/check_artifacts.py /tmp/stockcast-release-Jp8oV1/final-dist
/tmp/stockcast-release-Jp8oV1/tools/bin/python tests/release/run_notebooks.py /tmp/stockcast-release-Jp8oV1/current-notebooks
# After correcting 06's schema, resume the remaining notebooks in a new directory:
/tmp/stockcast-release-Jp8oV1/tools/bin/python tests/release/run_notebooks.py /tmp/stockcast-release-Jp8oV1/current-tail --match '[01][06789]*.ipynb'
PYTHONPATH=src /tmp/stockcast-release-Jp8oV1/tools/bin/mkdocs build --strict --site-dir /tmp/stockcast-release-Jp8oV1/final-site
```

For each of `wheel-env` and `sdist-env`, installed the corresponding final
artifact, ran `python -m pip check`, and invoked
`python -I /home/filtheo/stockcast/tests/release/artifact_smoke.py`.
The `current-notebooks`, `current-tail`, and `valid-04c-accepted` reports
jointly cover all twelve current sources, with the last report superseding
04c's earlier result. A final SHA-256 comparison confirmed all twelve current
notebooks match successful execution reports (44 figures total). A byte-level
comparison also confirmed every current `src/stockcast/**/*.py` file matches the
tested wheel. The artifact checker passed again: 28 wheel members and 43 sdist
members. The first notebook run stopped at 06's invalid schema before its
correction. Notebook 04c's local completion row was updated only after execution,
focused tests, and figure review. Specification 92 is currently ignored by Git;
its local update is not part of the tracked release diff.

Final local artifact SHA-256:

```text
d6826548ce1f0fa4b120c1e3f33b7a42d7bd266d44c84f3ddf2c3496022bc989  stockcast-0.1.0-py3-none-any.whl
8f989ba5cb4224012a167a54b435104170e7d77360c7379d5ec3ada751e270fc  stockcast-0.1.0.tar.gz
```
