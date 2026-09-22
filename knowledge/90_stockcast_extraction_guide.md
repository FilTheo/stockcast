# 90 — Stockcast extraction guide

Current-status note (2026-09-22): the extraction decisions below describe the
earlier staging phase. Their no-build gate, undecided URL/version support,
and candidate-API wording are superseded by the current `pyproject.toml`,
knowledge 93, and the owner's release-readiness audit request. Local builds
and tests are authorized; publication is not authorized by this audit.
See `RELEASE_READINESS.md` for fresh evidence and remaining decisions.

## 90.1 Confirmed decisions

- Project/package name: **Stockcast**.
- Distribution and import namespace: `stockcast`; no `pystate` compatibility
  package.
- First public version: `0.1.0`.
- Source layout: `src/stockcast/` with the existing five subsystem directories;
  tests, docs, and examples live outside `src/`.
- License: **Apache License 2.0**.
- Existing implementation/tests remain in the main tree; old documentation and
  reports are preserved under `arxiv/legacy_inventory`.
- The future public package is limited to the reusable inventory core. SPAR,
  company adapters, data, and experiments remain private main-tree context.
- Active root files should be fresh, minimal, and useful for navigation.
- Do not build the package yet.
- Repository/project URL remains open; do not invent a placeholder URL.
- The unrestricted legacy `after_step` hook is removed from the staging core.
- Keep `ContinuousReviewPolicy` as the public name while stating that it is
  evaluated once per simulated period, not continuously in physical time.
- Do not expose the removed forecast/history opening-state initializer names or
  the ambiguous `backorder_units_end` metric. Use explicit zero/observed state
  initialization and the precise `backlog_unit_periods` or
  `terminal_backlog_units` metric.
- Stockcast 0.1.0 will include a small ordered callback interface whose first
  intervention point is after policy prediction. The engine must validate,
  apply, audit, and record typed callback adjustments; callbacks must not
  directly mutate live inventory or finalized events.
- The approved callback surface also includes a separate typed on-hand phase
  after demand and before ordering, the four scheduled built-ins documented in
  `tutorials/callbacks.md`, and exact callback-instance reset semantics.
- `SimulationEngine` 0.1.0 makes one composed order decision per enabled
  decision opportunity. Supplier-specific multiple decisions are deferred for
  a later typed design; low-level repeated-order accumulation remains intact.

## 90.2 Decisions still open

The approved identity and layout do not settle:

- distribution/import-name registry availability;
- minimum and supported Python versions;
- dependency floors/ceilings and optional dependency groups;
- top-level exports;
- build-time source provenance;
- Git hosting URL and release automation;
- maintainer and contact metadata;
- whether visualization ships in the base install or an extra;
- which experimental policy/metric semantics are public and stable;

Agents must label proposals for these items and obtain a decision before
encoding them in fresh package metadata or API promises.

## 90.3 Candidate extraction boundary

Use the component boundary and exclusions in
[10.3–10.4](10_system_context.md#103-candidate-public-boundary). Start file-level
review from the 23 modules mapped in [70](70_module_reference.md), with selected
core unit tests. This is an allowlist for review, not permission to publish the
rest of the private main tree.

Do not rewrite the scientifically hardened core from memory. A clean extraction
means a fresh public tree and metadata with reviewed copies/refactors of approved
code—not deletion of validated contracts or inclusion of private history.

The private extraction manifest now records the 45 files selected for the
first clean local history. The staging tree has been sanitized and initialized
as an independent repository on `main` with no remote. This establishes the
repository boundary; it does not approve packaging or publication.

## 90.4 Approved hierarchy and current state

```text
src/stockcast/
  core/
  policies/
  evaluation/
  visualization/
  utils/
tests/
  unit/
  regression/
docs/
  tutorials/
  reference/
  scientific-contracts/
```

The source tree and `tests/unit/` now exist in this layout. Regression tests,
public docs, and examples remain future work; the layout decision does not
approve package metadata or freeze the public API.

## 90.5 Extraction sequence

When authorized:

1. record the approved namespace, layout, and deferred hook contract;
2. create the staging package tree and migrate the client-independent tests;
3. preserve fail-closed scientific validation and add regression fixtures;
4. redesign exports intentionally rather than copying old `__init__.py` files;
5. create fresh Apache-2.0-compatible metadata without the current legacy URL
   or a fabricated replacement;
6. run source tests, artifact-content inspection, clean install tests, and
   public-example smoke tests;
7. review Git history and generated artifacts before any public push;
8. build tutorials and open documentation from this knowledge layer and
   executable examples.

## 90.6 Agent handoff checklist

Before proposing code changes, a new agent should be able to answer:

- What is the exact within-period order of receipt, backlog clearance, demand,
  review, constraints, order placement, event validation, and hooks?
- Which policy horizon applies to each target?
- Why are marginal-quantile summation and heuristic opening stock forbidden?
- Which event identities prove stock-flow consistency?
- Which parts are candidate public core versus private main-tree business
  adapters?
- Which behaviors are confirmed, proposed, or known defective?

If not, follow the task table in
[the knowledge index](00_index.md#01-minimal-reading-path) and inspect the cited
source before editing.

## 90.7 Definition of ready-to-build

Distribution construction should begin only after there is an explicit decision log
for the remaining API/build questions, a reviewed extraction file list, and a
test split that excludes private adapters without weakening core evidence. The
legacy live-state hook surface has been removed and replaced by engine-applied,
typed callback results. The callback implementation and evidence still require
owner review before that release gate is closed. Until the remaining conditions
are met, documentation and read-only design work may continue, but package
artifacts should not be produced.
