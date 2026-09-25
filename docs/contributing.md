# Contributing

Contributions are welcome, from typo fixes to new building blocks. Open an
issue or pull request on the [GitHub repository](https://github.com/FilTheo/stockcast).

## Development setup

```bash
git clone https://github.com/FilTheo/stockcast.git
cd stockcast
python -m pip install -e . pytest
python -m pytest -p no:cacheprovider -q -o addopts='' tests/unit
```

The release checks run the tests on Python 3.10 to 3.13, and on Python 3.10
with the oldest supported NumPy, pandas, and Matplotlib.

## What a change needs

- **Tests** for new behaviour.
- **Documentation** in the same pull request when public behaviour changes:
  arguments, outputs, errors, or documented semantics.
- **Compatibility.** Public imports, output columns, metrics, and documented
  behaviour keep their meaning within 0.1.x. New features are optional
  arguments or new objects, so existing code and results stay identical.

## Working on the documentation

The site is built with [Material for MkDocs](https://squidfunk.github.io/mkdocs-material/)
and [mkdocstrings](https://mkdocstrings.github.io/).

```bash
python -m pip install -e . -r requirements-docs.txt
mkdocs serve                          # live preview at http://127.0.0.1:8000
mkdocs build --strict                 # what CI runs
python -m pytest -q tests/docs        # runs every python code block in the docs
```

How the docs are organised:

| Folder | Purpose | Style |
|---|---|---|
| `docs/get-started/`, `docs/learn/` | first contact and the 8-step series | one idea per page, the tea-shop example throughout |
| `docs/user-guide/` | concepts and building blocks | idea → math → code → notebooks |
| `docs/user-guide/design/` | the reasoning behind core choices | short essays |
| `docs/how-to/` | task recipes | straight to the point |
| `docs/reference/` | generated API pages | driven by docstrings (Google style) |
| `docs/notebooks/` | link to `examples/notebooks`, rendered with mkdocs-jupyter | executed notebooks |

Conventions:

- **Code runs.** Every ` ```python ` block is executed by `tests/docs`, page
  by page, like notebook cells. The README's quickstart is executed too, and
  `tests/release/artifact_smoke.py` repeats it against built packages. Use ` ```py ` for snippets that should not run,
  for example ones that need an optional forecasting package.
- **Shared setup.** `docs/includes/learn-setup.py` holds the tea-shop scenario.
  Include it in a collapsed block with `--8<-- "learn-setup.py"`.
- **Real output.** Output blocks (` ```text `) are pasted from running the
  code.
- **Figures** are drawn from real runs by `docs/scripts/make_figures.py`:
  `python docs/scripts/make_figures.py` regenerates all of them.
- **Imports** come from the home module of each name
  (`from stockcast.core import SimulationEngine`).
- **Tone**: friendly and confident. Explain the idea, show the numbers, link
  to the notebook.

## Publishing

Every push to `main` that touches the docs, the notebooks, or the source runs
the doc tests, builds the site strictly, and publishes it to GitHub Pages with
GitHub Actions (`.github/workflows/docs.yml`). Pull requests run the same
checks without publishing.

## Notebooks

To execute the notebook collection against an installed wheel, use Python
3.11+ with `smooth`, `nbclient`, and `ipykernel` installed:

```bash
python tests/release/run_notebooks.py /tmp/stockcast-notebook-review
```

Use a new output directory for each run. The runner keeps executed copies,
figures, warnings, and source hashes; it does not edit the example notebooks.
