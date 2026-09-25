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

## Check that it works

```python
from stockcast.core import SimulationEngine
from stockcast.policies import OrderUpToPolicy

print(SimulationEngine, OrderUpToPolicy)
```

If this prints two class names, you are ready for the
[Quickstart](quickstart.md).

## Optional: a forecasting library

Stockcast does not fit forecasts, so it does not install a forecasting
library. Use the one you already know. Several notebooks use
[smooth](https://openforecast.org/smooth-py/) (`pip install smooth`), a Python
implementation of state-space forecasting models from the same open-source
ecosystem. See [Connect any forecasting model](../how-to/connect-a-forecaster.md).

## Running the notebooks

The [example notebooks](../tutorials/index.md) live in
[`examples/notebooks`](https://github.com/FilTheo/stockcast/tree/main/examples/notebooks).
Clone the repository, install Stockcast, then install Jupyter and smooth:

```bash
pip install jupyterlab smooth
jupyter lab examples/notebooks
```

## Import style

Each public name has a home module. The examples in these docs always import
from it, so you can see where every object comes from:

| Module | What lives there |
|---|---|
| `stockcast.core` | inventory state, orders, the simulation engine, schedules, constraints, callbacks, suppliers, processes |
| `stockcast.policies` | built-in replenishment policies and target providers |
| `stockcast.evaluation` | the evaluator, metrics, and ledger validation |
| `stockcast.utils` | the demand generator and manual-loop helpers |
| `stockcast.visualization` | ready-made plots |
