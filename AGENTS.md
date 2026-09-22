  # Pyforia agent guide

## A. Status and authority

This is a staging repository. The active source tree uses the `pyforia`
namespace, but there is no installable Pyforia distribution yet.

Use sources in this order:

1. The current user's explicit instructions.
2. Fresh root documentation (`README.md`, this file, `knowledge/`, and `docs/`).
3. Tested staging code and tests under `src/` and `tests/`.
4. Archived documentation and reports only as historical evidence.

If code, tests, and documentation disagree, report the disagreement. Do not
silently change scientific behavior, public API semantics, archive boundaries,
or unresolved release choices.

## B. Required reading path

Read [`knowledge/00_index.md`](knowledge/00_index.md), then follow its
task-specific path. The numbered `knowledge/` documents are the internal agent
orientation layer. Do not read all of them by default. For package extraction,
read knowledge documents `10`, `90`, and the task-relevant subsystem pages.
[`docs/index.md`](docs/index.md) is the public-documentation site entry point.
It must be grounded in active code, tests, and executable examples; it is not
an implementation authority.

## C. Non-negotiable inherited contracts

- Fail closed on missing scientific inputs; never invent demand, uncertainty,
  opening state, costs, timing, or provenance.
- Do not restore the removed `0.3 * mean` uncertainty heuristic.
- Do not sum marginal forecast quantiles into a protection-period quantile.
- Lead time is a positive integer; same-period replenishment is not implemented
  in the current staging baseline.
- Demand calendars, SKU identifiers, dates, frequency, initial decision,
  experiment windows, costs, constraints, and random seed state are explicit.
- Treat validated event rows as accounting evidence. Preserve physical-stock,
  backlog, pipeline, lost-sales, and constraint identities.
- Keep SPAR, company adapters/data, private experiments, reports, and old Git
  history out of any future public extraction.

## D. Change workflow

1. State which indexed contract or decision the change implements.
2. Inspect current files; do not rely only on archived prose.
3. Ask the user before deciding uncertain scientific meaning, compatibility,
   public promises, removals, naming details, or repository identity.
4. Keep edits narrow and preserve unrelated work.
5. Add or update tests when implementation begins.
6. Report exact commands and results; distinguish current evidence from
   historical evidence.
7. Before completing, perform the documentation-impact check in §E and
   report its outcome.

The archive is read-only documentation evidence by default. Do not modernize
files inside it to make old prose appear current. The approved staging
implementation is under `src/pyforia/`; do not package or redesign it further
without explicit authorization.

## E. Documentation workflow

Agent-facing documents in `knowledge/` describe evidence, connections, flows,
and constraints. They are not public tutorials. Keep their numeric hierarchy
and update the module/test coverage pages when implementation changes. When the
package exists, build user documentation in `docs/` from active code, tests,
and executable examples. Never copy unsupported claims from archived reports
or changelogs.

Every implementation/code task must perform a documentation-impact check
before completion. When a change affects public/user-visible behavior, APIs,
parameters, return values, errors, workflows, installation, examples, or
documented scientific semantics, update the relevant public documentation
under `docs/` in the SAME change. When it affects internal
architecture/contracts, execution flows, module responsibilities, or
test/evidence coverage, update the relevant `knowledge/` document(s) where
appropriate. Do NOT modify documentation merely because code changed
internally if the documented behavior is unchanged. At task completion,
explicitly report either "Documentation updated: <files>" or
"Documentation impact: none". Documentation must reflect active code/tests
and must not claim behavior that has not been implemented/tested.
