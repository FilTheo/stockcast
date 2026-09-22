# Contributing

Pyforia welcomes focused issues and contributions through the
[GitHub repository](https://github.com/FilTheo/pyforia).

Before proposing a change, identify whether it affects a frozen public import,
output schema, metric, timing rule, or documented workflow. Public changes need
tests and documentation; private helpers and implementation modules are not
public extension points.

## Local checks

Run the source contract suite from the repository root:

```bash
python -m pip install -e . pytest
python -m pytest -p no:cacheprovider -q -o addopts='' tests/unit
```

Build the documentation after installing `requirements-docs.txt`:

```bash
python -m pip install -r requirements-docs.txt
mkdocs build --strict
```

Source-path checks do not replace clean wheel/sdist validation. That release
check is performed separately before publication.

The release-check workflow exercises Python 3.10–3.13 and the declared NumPy,
pandas, and Matplotlib floors on Python 3.10. Publishing validates artifact
contents, runs the tests against the built wheel, and smoke-tests a separate
source-distribution installation before uploading artifacts. A published GitHub
release must use a `v`-prefixed tag matching `project.version`.

To execute the notebook collection against an installed wheel, use Python
3.11+ with `smooth==1.0.7`, `nbclient`, and `ipykernel` installed:

```bash
python tests/release/run_notebooks.py /tmp/pyforia-notebook-review
```

Use a new output directory for each run. The runner retains executed copies,
figures, warnings, and source hashes; it does not edit the example notebooks.
Successful execution still requires scientific and visual review before a
notebook is accepted as release evidence.
