"""Execute examples against an installed package, retaining review artifacts.

Run with a Python 3.11+ environment containing Pyforia, smooth==1.0.7,
nbclient and ipykernel. Output belongs outside the source tree. Each notebook
gets a fresh working directory containing only the declared example assets.
"""

import argparse
import base64
import hashlib
import importlib.metadata
import json
import os
import shutil
import sys
import time
from pathlib import Path

import nbformat
from IPython.core.inputtransformer2 import TransformerManager
from nbclient import NotebookClient


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--match", default="*.ipynb")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    output = args.output.resolve()
    if output == root or root in output.parents:
        parser.error("output must be outside the repository")
    output.mkdir(parents=True, exist_ok=True)
    if importlib.metadata.version("smooth") != "1.0.7":
        raise RuntimeError("Notebook contract requires smooth==1.0.7")
    os.environ.pop("PYTHONPATH", None)
    os.environ["PATH"] = str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"]
    os.environ["MPLCONFIGDIR"] = str(output / "matplotlib")
    os.environ["IPYTHONDIR"] = str(output / "ipython")
    os.environ["JUPYTER_RUNTIME_DIR"] = str(output / "runtime")
    reports = []
    paths = sorted((root / "examples/notebooks").glob(args.match))
    if not paths:
        parser.error("no notebooks matched")
    for path in paths:
        started = time.monotonic()
        work = output / path.stem
        work.mkdir(exist_ok=False)
        shutil.copytree(root / "examples/notebooks/data", work / "examples/notebooks/data")
        notebook = nbformat.read(path, as_version=4)
        nbformat.validate(notebook)
        for index, cell in enumerate(notebook.cells):
            if cell.cell_type == "code":
                compile(TransformerManager().transform_cell(cell.source), f"{path.name}:{index}", "exec")
                cell.outputs = []
                cell.execution_count = None
        # Explicitly prove the kernel imports the distribution, not this checkout.
        notebook.cells.insert(0, nbformat.v4.new_code_cell(
            "import pathlib, pyforia, sys\n"
            f"assert not pathlib.Path(pyforia.__file__).resolve().is_relative_to(pathlib.Path({str(root)!r}))\n"
            "print('Installed package:', pyforia.__file__, '| Python:', sys.version)\n"
            "%matplotlib inline"
        ))
        report = {"notebook": path.name, "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        print(f"START {path.name}", flush=True)
        try:
            NotebookClient(notebook, timeout=1800, kernel_name="python3",
                           resources={"metadata": {"path": str(work)}}).execute()
            report["status"] = "passed"
        except Exception as exc:  # noqa: BLE001 -- retain failures and continue the audit
            report.update(status="failed", error=str(exc))
        figures, stderr = [], []
        for index, cell in enumerate(notebook.cells):
            for item in cell.get("outputs", []):
                if item.output_type == "stream" and item.name == "stderr":
                    stderr.append(item.text)
                png = item.get("data", {}).get("image/png")
                if png:
                    figure = work / f"cell-{index:03d}-figure-{len(figures):02d}.png"
                    figure.write_bytes(base64.b64decode(png))
                    figures.append(str(figure))
        report.update(seconds=round(time.monotonic() - started, 2), figures=figures, stderr=stderr)
        nbformat.write(notebook, work / path.name)
        reports.append(report)
        (output / "results.json").write_text(json.dumps(reports, indent=2) + "\n")
        print(f"{report['status'].upper()} {path.name}: {len(figures)} figures, {len(stderr)} stderr streams, {report['seconds']}s", flush=True)
    if any(report["status"] != "passed" for report in reports):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
