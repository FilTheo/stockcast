# Stockcast examples

Twenty-two runnable notebooks, from a first simulation to multi-supplier,
perishable, and production workflows. Each one is also rendered, with its
outputs, on the [Examples](https://filtheo.github.io/stockcast/tutorials/)
page of the documentation, which lists what every notebook teaches and the
building blocks it uses.

## Running them

Clone the repository, install Stockcast, then install the notebook extras and
open `examples/notebooks` from the repository root:

```bash
pip install -e .
pip install jupyterlab smooth
jupyter lab examples/notebooks
```

Notebooks 04e and 09 use smooth's simulated intervals, so their simulated
targets can differ slightly between runs.

## Contents

| Group | Notebooks |
|---|---|
| Foundations | 01 inventory flow · 02 first engine simulation · 02b decision schedules · 02c synthetic demand · 03 your own loop |
| Forecasts to orders | 04 weekly forecast to order · 04b daily newsvendor · 04c cumulative protection target · 04d rolling cumulative targets · 04e cumulative target methods · 04f scheduled forecast simulation |
| Extending Stockcast | 05 custom policies · 05b reorder points and review frequency · 05c extension points · 05d open orders and suppliers · 05e inventory processes · 05f unreliable supplier |
| Experiments and operations | 06 fair comparisons · 07 FIFO shelf life on M5 demand · 08 callbacks and audit · 09 full operational experiment · 10 production daily close |

## Data

`notebooks/data/m5/` holds the small M5-derived inputs used by Notebooks 07,
09, and 10. They live in the repository for reproducible examples and are not
part of the installed package. See the [data README](notebooks/data/m5/README.md)
for attribution and scope.
