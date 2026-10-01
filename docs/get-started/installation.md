# Installation

Stockcast runs on Python 3.10 or later. Its only dependencies are NumPy,
pandas, and Matplotlib.

=== "pip (PyPI)"

    ```bash
    pip install stockcast
    ```

=== "From GitHub"

    Install the current development version straight from the repository:

    ```bash
    pip install "git+https://github.com/FilTheo/stockcast.git"
    ```

=== "Editable (contributors)"

    Clone the repository and install it in editable mode:

    ```bash
    git clone https://github.com/FilTheo/stockcast.git
    cd stockcast
    pip install -e .
    ```

## Verify the installation

```python
import stockcast
from stockcast.core import SimulationEngine
from stockcast.policies import OrderUpToPolicy

print(stockcast.__version__, SimulationEngine, OrderUpToPolicy)
```

If this prints the version and two class names, you are ready for the
[Quickstart](quickstart.md).

## Forecasting libraries

Stockcast does not fit forecasts, so it does not install a forecasting
library. Use the one you already know. Several notebooks use
[smooth](https://openforecast.org/smooth-py/) (`pip install "smooth>=1.0.7"`), a Python
implementation of state-space forecasting models from the same open-source
ecosystem. See [Connect any forecasting model](../how-to/connect-a-forecaster.md).

## Notebooks

The [example notebooks](../tutorials/index.md) live in
[`examples/notebooks`](https://github.com/FilTheo/stockcast/tree/main/examples/notebooks).
Clone the repository, install Stockcast, then install Jupyter and smooth:

```bash
pip install jupyterlab "smooth>=1.0.7"
jupyter lab examples/notebooks
```

## Imports

Each public name has a home module, and the docs import from it:

| Module | What lives there |
|---|---|
| `stockcast.core` | inventory state, orders, the simulation engine, schedules, constraints, callbacks, suppliers, processes |
| `stockcast.policies` | built-in replenishment policies and target providers |
| `stockcast.evaluation` | the evaluator, metrics, and event table validation |
| `stockcast.utils` | the demand generator and manual-loop helpers |
| `stockcast.visualization` | ready-made plots |
