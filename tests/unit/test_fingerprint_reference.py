"""Fingerprints equal Stockcast 0.1.0's, over a matrix of column dtypes.

The 0.2.0 fast paths in ``SimulationEngine._needs_encoding`` and
``SimulationEngine._sorted_rows`` must give exactly the row order and the
checksum of 0.1.0. The reference below is a frozen copy of the 0.1.0 code
(``git show 21c27aa:src/stockcast/core/simulation_engine.py``), so the test
does not compare the new code with itself. ``_canonical_cell`` is unchanged
since 0.1.0 and is shared.
"""

from __future__ import annotations

import hashlib
import json
from typing import List

import numpy as np
import pandas as pd
import pytest

from stockcast.core import SimulationEngine

# ---------------------------------------------------------------------------
# Frozen 0.1.0 reference (21c27aa), verbatim apart from being module functions
# ---------------------------------------------------------------------------


def reference_needs_encoding(column: pd.Series) -> bool:
    dtype = column.dtype
    if isinstance(dtype, pd.CategoricalDtype):
        return reference_needs_encoding(pd.Series(dtype.categories))
    if pd.api.types.is_object_dtype(dtype):
        return pd.api.types.infer_dtype(column, skipna=False) not in {"string", "empty"}
    if isinstance(dtype, np.dtype) and (
        (dtype.kind == "f" and dtype.itemsize > 8)
        or (dtype.kind == "c" and dtype.itemsize > 16)
    ):
        return True  # extended precision: older pandas rounds it to float64
    try:
        pd.util.hash_pandas_object(column, index=False)
    except Exception:  # e.g. extended precision: pandas cannot hash it
        return True
    return False


def reference_sorted_rows(frame: pd.DataFrame, sort_columns: List[str]) -> pd.DataFrame:
    try:
        return frame.sort_values(sort_columns, kind="stable").reset_index(drop=True)
    except Exception:  # mixed types, float16 keys, duplicate column names
        pass
    positions = [
        position
        for name in dict.fromkeys(sort_columns)
        for position, column in enumerate(frame.columns)
        if column == name
    ]
    if not positions:
        return frame.reset_index(drop=True)
    keys = pd.DataFrame({
        rank: frame.iloc[:, position].map(SimulationEngine._canonical_cell).tolist()
        for rank, position in enumerate(positions)
    })
    order = keys.sort_values(list(keys.columns), kind="stable").index
    return frame.iloc[order].reset_index(drop=True)


def reference_checksum(frame: pd.DataFrame, sort_columns: List[str]) -> str:
    encoded = [
        position for position in range(frame.shape[1])
        if reference_needs_encoding(frame.iloc[:, position])
    ]
    if encoded:
        frame = frame.copy()
        for position in encoded:
            frame.isetitem(
                position, frame.iloc[:, position].map(SimulationEngine._canonical_cell)
            )
    ordered = reference_sorted_rows(frame, sort_columns)
    row_hashes = pd.util.hash_pandas_object(ordered, index=True).to_numpy()
    hasher = hashlib.sha256()
    hasher.update("|".join(map(str, ordered.columns)).encode("utf-8"))
    hasher.update("|".join(map(str, ordered.dtypes)).encode("utf-8"))
    if encoded:
        hasher.update(("encoded:" + json.dumps(encoded)).encode("utf-8"))
    hasher.update(row_hashes.tobytes())
    return hasher.hexdigest()


# ---------------------------------------------------------------------------
# The dtype matrix
# ---------------------------------------------------------------------------

def _dates(unit):
    return pd.Series(pd.to_datetime(["2025-01-01", "2025-01-02", "2025-01-03", "2025-01-04"])
                     .to_numpy().astype(f"datetime64[{unit}]"))


# Four ascending distinct values per dtype (bool has two), and a missing value
# where the dtype has one. Numbers and durations are chosen so that their
# text order differs from their value order ("10" < "2"): 0.1.0 falls back to
# a text-based order when pandas cannot sort, and that must stay visible.
COLUMNS = {
    **{name: (lambda name=name: pd.Series([2, 10, 30, 100], dtype=name), None)
       for name in ("int8", "int16", "int32", "int64", "uint8", "uint16", "uint32", "uint64")},
    "bool": (lambda: pd.Series([False, False, True, True]), None),
    **{name: (lambda name=name: pd.Series([0.5, 2.5, 10.5, 100.5], dtype=name), np.nan)
       for name in ("float16", "float32", "float64")},
    "datetime64[ns]": (lambda: _dates("ns"), pd.NaT),
    "datetime64[s]": (lambda: _dates("s"), pd.NaT),
    "timedelta64[ns]": (lambda: pd.Series(pd.to_timedelta([1, 2, 10, 30], unit="D")), pd.NaT),
    "object text": (lambda: pd.Series(["a", "b", "c", "d"], dtype=object), None),
    "string": (lambda: pd.Series(pd.array(["a", "b", "c", "d"], dtype="string")), pd.NA),
    # pandas 3 infers its default str dtype here; earlier versions give object.
    "inferred text": (lambda: pd.Series(["a", "b", "c", "d"]), None),
    "categorical": (lambda: pd.Series(pd.Categorical(["a", "b", "c", "d"])), np.nan),
    "categorical, reversed categories": (
        lambda: pd.Series(pd.Categorical(["a", "b", "c", "d"], categories=["d", "c", "b", "a"])),
        np.nan,
    ),
    "tuple ids": (lambda: pd.Series([("a", 1), ("a", 2), ("b", 0), ("b", 1)], dtype=object), None),
    "mixed objects": (lambda: pd.Series([1, "a", 2.5, "b"], dtype=object), None),
}

ORDERS = {
    "sorted": [0, 1, 2, 3],
    "unsorted": [2, 0, 3, 1],
    "tied": [0, 0, 1, 2],
    "sorted, missing last": [0, 1, 2, None],
    "sorted, missing first": [None, 1, 2, 3],
}


def _column(dtype_name: str, order: str):
    """A column of the dtype in the given order, or None when it does not apply."""
    make, missing = COLUMNS[dtype_name]
    base = make()
    positions = ORDERS[order]
    if None in positions and missing is None:
        return None
    values = base.iloc[[0 if p is None else p for p in positions]].reset_index(drop=True)
    if None in positions:
        values = values.astype(object) if values.dtype == object else values
        values[positions.index(None)] = missing
    return values


def _outcome(function, *args):
    try:
        return function(*args)
    except Exception as error:  # the same error must occur
        return f"error {type(error).__name__}"


def _same(new, reference) -> bool:
    if isinstance(new, str) or isinstance(reference, str):
        return new == reference
    try:
        pd.testing.assert_frame_equal(new, reference, check_exact=True)
        return True
    except AssertionError:
        return False


def _mismatches(frame: pd.DataFrame, sort_columns: List[str]) -> List[str]:
    found = []
    rows = _outcome(SimulationEngine._sorted_rows, frame, sort_columns)
    if not _same(rows, _outcome(reference_sorted_rows, frame, sort_columns)):
        found.append("row order")
    checksum = _outcome(SimulationEngine._dataframe_checksum, frame, sort_columns)
    if checksum != _outcome(reference_checksum, frame, sort_columns):
        found.append("checksum")
    return found


@pytest.mark.parametrize("first", list(COLUMNS))
def test_fingerprints_equal_the_0_1_0_reference(first):
    failures = []
    for order in ORDERS:
        key = _column(first, order)
        if key is None:
            continue
        index = [10, 3, 7, 1]  # an index that renumbering must drop
        # One key.
        frame = pd.DataFrame({"unique_id": key,
                              "S": [10.0, 20.0, 30.0, 40.0]}).set_axis(index)
        for problem in _mismatches(frame, ["unique_id"]):
            failures.append(f"{order}, one key: {problem}")
        # Two keys: every dtype as the second key, descending so ties matter.
        for second in COLUMNS:
            later = _column(second, "unsorted")
            frame = pd.DataFrame({"unique_id": key, "fh": later,
                                  "S": [10.0, 20.0, 30.0, 40.0]}).set_axis(index)
            for problem in _mismatches(frame, ["unique_id", "fh"]):
                failures.append(f"{order}, second key {second}: {problem}")
        # Duplicate column names.
        duplicated = pd.concat(
            [pd.DataFrame({"unique_id": key}), pd.DataFrame({"unique_id": key})], axis=1,
        ).set_axis(index)
        for problem in _mismatches(duplicated, ["unique_id"]):
            failures.append(f"{order}, duplicate columns: {problem}")
    assert not failures, failures


def test_the_reported_float16_case():
    frame = pd.DataFrame({
        "unique_id": [2, 10], "S": [10.0, 20.0], "fh": np.array([2, 2], dtype="float16"),
    })
    assert SimulationEngine._dataframe_checksum(frame, ["unique_id", "fh"]) == (
        reference_checksum(frame, ["unique_id", "fh"])
    )


@pytest.mark.parametrize("dtype_name", list(COLUMNS))
def test_needs_encoding_equals_the_0_1_0_reference(dtype_name):
    for order in ORDERS:
        column = _column(dtype_name, order)
        if column is not None:
            assert SimulationEngine._needs_encoding(column) == reference_needs_encoding(column)
