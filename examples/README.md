# Stockcast examples

The notebooks in `notebooks/` are runnable examples for a repository clone.
Run them from the repository root after installing Stockcast and any
notebook-specific optional dependencies.

`notebooks/data/m5/` contains the small M5-derived assets used by Notebooks 07,
09, and 10. They are versioned for reproducible GitHub examples but are
intentionally excluded from PyPI source distributions and wheels. See the
nearby data README for attribution and scope.

Notebook `02b_decision_schedules.ipynb` is a compact visual tour of periodic,
delayed, one-time, and irregular decisions. It also demonstrates using
`review_period` without constructing a scheduler object.

Notebook `04d_single_season_newsvendor.ipynb` connects artificial historical
demand to an empirical forecast, an economic quantile, and one newsvendor
purchase for a held-out selling week.

Notebook `05b_reorder_points_and_review_frequency.ipynb` sizes `(s,Q)` reorder
points over the schedule's protection window `L+1`. On one shared demand path,
it shows finer review steps approaching the continuous-review reorder point,
and the under-protection caused by copying the continuous-review formula.
