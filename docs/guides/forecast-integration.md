# Forecast integration with smooth

The forecast notebooks use [smooth](https://openforecast.org/smooth-py/) to
make the forecasting boundary concrete. It is a useful demonstration choice
because it provides an explicit fitted forecasting step and forecast output
that can be transformed into a dated inventory target. It is not a Stockcast
dependency and is not required for other workflows.

Install it separately when running those notebooks:

```bash
pip install smooth
```

The integration sequence is:

1. fit or obtain a forecast in your forecasting system;
2. calculate a protection-period target with a declared representation;
3. retain origin, frequency, probability, horizon, source, and end-date
   metadata;
4. fit the Stockcast policy with that target; and
5. evaluate the subsequent inventory outcome on a declared demand path.

The important boundary is not smooth-specific. Any forecasting package or
internal model can be used when it produces a target representation that meets
the policy contract. Notebook 04 shows one forecast-to-decision handoff;
Notebook 04b shows rolling refits and scheduled policy snapshots.
