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
