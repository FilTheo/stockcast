# M5-derived example inputs

`sample_1.csv` is a prepared subset of the M5 Forecasting Competition daily
unit-sales data. It is used only as demand observations in Notebooks 07, 09,
and 10; it does not supply inventory, shelf-life, cost, lead-time, or service
assumptions.

`notebook08_covariance_normal_checkpoint_2016-02-28.json` is a Stockcast
example checkpoint derived from the Notebook 09 scenario. It is not an M5
source artifact.

These files are intentionally committed for reproducible GitHub examples but
are excluded from source distributions and wheels. They are M5-derived data,
not Stockcast code, and are not covered by Stockcast's Apache-2.0 license; their
upstream terms continue to apply.

The maintainer confirmed permission to retain this subset in the repository
on 2026-09-22. This statement does not grant a new license to downstream users.
The subset preparation script is planned for a later addition.

Source and attribution: Makridakis, S., Spiliotis, E., & Assimakopoulos, V.
(2022). *M5 accuracy competition: Results, findings, and conclusions*.
International Journal of Forecasting, 38(4), 1346–1364.
https://doi.org/10.1016/j.ijforecast.2021.11.013. The competition materials are
published by the [M5 Methods project](https://github.com/Mcompetitions/M5-methods).
