"""Run every ``python`` code block in the documentation.

Each Markdown page is one test. Its ``python`` blocks run in order in one
shared namespace, like cells of a notebook, so a page can build its example
step by step. Blocks fenced as ``py`` are illustrations and are not run (for
example snippets that need an optional forecasting package).

Run with:

    python -m pytest -q tests/docs
"""

from __future__ import annotations

import os
import re
import textwrap
import warnings
from pathlib import Path

import pytest

DOCS = Path(__file__).resolve().parents[2] / "docs"
INCLUDES = DOCS / "includes"
SNIPPET = re.compile(r'^(?P<indent>[ \t]*)--8<-- "(?P<name>[^"]+)"[ \t]*$', re.MULTILINE)
FENCE = re.compile(r"^(?P<indent>[ \t]*)```(?P<lang>[A-Za-z0-9_+-]*)(?P<attrs>[^\n]*)$")


def python_blocks(text: str) -> list[str]:
    """Return the ``python`` fenced blocks of a Markdown page, dedented."""
    blocks, lines, index = [], text.splitlines(), 0
    while index < len(lines):
        opening = FENCE.match(lines[index])
        if not opening:
            index += 1
            continue
        indent, lang = opening["indent"], opening["lang"]
        body, index = [], index + 1
        while index < len(lines) and lines[index].strip() != "```":
            body.append(lines[index])
            index += 1
        index += 1
        if lang == "python":
            code = "\n".join(line[len(indent):] if line.startswith(indent) else line
                             for line in body)
            # Strip Material code-annotation markers such as "# (1)!".
            code = re.sub(r"#\s*\(\d+\)!?", "", textwrap.dedent(code))
            # Expand pymdownx.snippets includes, as the site build does.
            code = SNIPPET.sub(
                lambda match: textwrap.indent(
                    (INCLUDES / match["name"]).read_text(encoding="utf-8"), match["indent"]
                ),
                code,
            )
            blocks.append(code)
    return blocks


README = DOCS.parent / "README.md"
PAGES = sorted(
    path for path in DOCS.rglob("*.md")
    if "notebooks" not in path.relative_to(DOCS).parts
    and "includes" not in path.relative_to(DOCS).parts
    and python_blocks(path.read_text(encoding="utf-8"))
) + [README]


@pytest.mark.parametrize("page", PAGES, ids=lambda p: str(p.relative_to(DOCS.parent)))
def test_page_examples_run(page: Path, tmp_path, monkeypatch) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    monkeypatch.chdir(tmp_path)
    namespace: dict = {"__name__": "__docs__"}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        for number, code in enumerate(python_blocks(page.read_text(encoding="utf-8")), 1):
            try:
                exec(compile(code, f"{page.name}[block {number}]", "exec"), namespace)
            except Exception as error:  # pragma: no cover - failure path
                raise AssertionError(
                    f"{page.relative_to(DOCS.parent)}: block {number} failed\n\n{code}"
                ) from error
            finally:
                plt.close("all")


def test_pages_were_found() -> None:
    assert PAGES, f"no documentation pages with python blocks under {DOCS}"
    assert os.path.isdir(DOCS)
