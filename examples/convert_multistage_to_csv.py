"""Convert the multi-stage pickles to the CSV files that OSL stores.

Each CSV holds exactly the bytes of ``dataset.serialize_data("csv")`` for the
dataset that ``upload_multistage_dataset_OSL.py`` builds, so a dashboard can read
the cells from disk instead of the wiki. Building ``StepTypedCyclingDataRow``
objects takes about 8 minutes per cell, so the script writes the CSV from the
pint-pandas frame of ``build_frame`` instead: the columns are converted to the
units and column order of ``to_df()`` (SI units, ``step_time`` and ``energy``
as empty columns).

The frame is validated with the rules of ``validate`` in the upload script,
applied to the columns instead of the rows. A cell that fails is logged, gets no
CSV, and is listed at the end. Cells in ``SKIPPED_CELLS`` are not converted.

Also writes ``index.json`` with ``{cell, c_rate, procedure, rows, file}`` per
cell. Run from the ``examples`` directory::

    python convert_multistage_to_csv.py
    python convert_multistage_to_csv.py --cells 4-3 1-6
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd
from upload_multistage_dataset_OSL import (
    DATA_DIR,
    SKIPPED_CELLS,
    build_entities,
    build_frame,
    first_stage_c_rate,
    load_cell,
)

from opensemantic.base import _pint_csv

OUT_DIR = DATA_DIR / "csv_osl"

# Columns and units of ``to_df()`` of a cycling dataset, in its order. Columns
# that ``build_frame`` does not fill stay empty.
DF_COLUMNS = {
    "test_time": "second",
    "voltage": "volt",
    "current": "ampere",
    "cycle_count": "dimensionless",
    "step_count": "dimensionless",
    "step_time": "second",
    "capacity": "coulomb",
    "energy": "joule",
    "step_type": None,
}


def validate_frame(frame: pd.DataFrame) -> None:
    """Raise ``ValueError`` on the first violated rule (see ``validate``)."""
    required = [
        "voltage",
        "current",
        "test_time",
        "capacity",
        "cycle_count",
        "step_type",
    ]
    if len(frame) == 0:
        raise ValueError("no rows")
    for field in required:
        missing = frame[field].isna().to_numpy()
        if missing.any():
            raise ValueError(f"row {int(missing.argmax())}: missing {field}")

    capacity = frame["capacity"].pint.to("milliampere_hour").pint.magnitude
    capacity = capacity.to_numpy(dtype=float)
    negative = capacity < 0
    if negative.any():
        i = int(negative.argmax())
        raise ValueError(f"row {i}: negative capacity {capacity[i]}")

    time_s = frame["test_time"].pint.to("second").pint.magnitude
    time_s = time_s.to_numpy(dtype=float)
    cycle = frame["cycle_count"].pint.magnitude.to_numpy(dtype=float)
    if cycle[0] != 0:
        raise ValueError(f"first cycle_count is {cycle[0]}, expected 0")
    bad_time = time_s[1:] <= time_s[:-1]
    if bad_time.any():
        i = int(bad_time.argmax()) + 1
        raise ValueError(f"row {i}: test_time {time_s[i]} <= {time_s[i - 1]}")
    step = cycle[1:] - cycle[:-1]
    bad_cycle = (step != 0) & (step != 1)
    if bad_cycle.any():
        i = int(bad_cycle.argmax()) + 1
        raise ValueError(f"row {i}: cycle {cycle[i]} after {cycle[i - 1]}")

    step_type = frame["step_type"].to_numpy(dtype=object)
    changed = np.flatnonzero(step_type[1:] != step_type[:-1]) + 1
    previous = capacity[changed - 1]
    bad_start = ~((capacity[changed] < previous) | (capacity[changed] < 1.0))
    if bad_start.any():
        k = int(bad_start.argmax())
        raise ValueError(
            f"row {changed[k]}: capacity {capacity[changed[k]]} at a new segment "
            f"is not below {previous[k]} and not near 0"
        )


def to_df_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """The frame in the columns, units and dtypes of ``Dataset.to_df()``."""
    columns = {}
    for name, unit in DF_COLUMNS.items():
        if unit is None:
            columns[name] = frame[name].astype(object)
            continue
        dtype = f"pint[{unit}][Float64]"
        if name in frame.columns:
            columns[name] = frame[name].pint.to(unit).astype(dtype)
        else:
            columns[name] = pd.Series([pd.NA] * len(frame), dtype=dtype)
    return pd.DataFrame(columns)


def convert_cell(cell_id: str, data_dir: Path, out_dir: Path) -> Dict:
    cycles = load_cell(data_dir / f"{cell_id}.pkl")[cell_id]["data"]
    c_rate = first_stage_c_rate(cycles)
    _, _, procedure = build_entities(cell_id, c_rate)
    frame = build_frame(cycles, None)
    validate_frame(frame)
    payload = _pint_csv.to_csv(to_df_frame(frame))
    (out_dir / f"{cell_id}.csv").write_bytes(payload)
    return {
        "cell": cell_id,
        "c_rate": c_rate,
        "procedure": procedure,
        "rows": len(frame),
        "file": f"{cell_id}.csv",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument("--cells", nargs="*", help="cell ids, default: all")
    args = parser.parse_args()

    out_dir = args.out_dir or args.data_dir / "csv_osl"
    out_dir.mkdir(parents=True, exist_ok=True)
    cells = args.cells or sorted(p.stem for p in args.data_dir.glob("*.pkl"))
    cells = [c for c in cells if c not in SKIPPED_CELLS]

    index: List[Dict] = []
    failures: Dict[str, str] = {}
    began = time.perf_counter()
    for cell_id in cells:
        start = time.perf_counter()
        try:
            entry = convert_cell(cell_id, args.data_dir, out_dir)
        except Exception as error:  # noqa: BLE001 - log and continue
            failures[cell_id] = f"{type(error).__name__}: {error}"
            print(f"[{cell_id}] FAILED: {failures[cell_id]}", flush=True)
            continue
        index.append(entry)
        print(
            f"[{cell_id}] {entry['rows']} rows, {entry['c_rate']}C, "
            f"{time.perf_counter() - start:.1f} s",
            flush=True,
        )
    # merge into an existing index, so a run with --cells keeps the other cells;
    # a cell that failed now loses its entry
    index_path = out_dir / "index.json"
    previous = (
        json.loads(index_path.read_text(encoding="utf-8"))
        if index_path.exists()
        else []
    )
    done = {entry["cell"] for entry in index} | set(failures)
    merged = [entry for entry in previous if entry["cell"] not in done] + index
    merged.sort(key=lambda entry: [int(part) for part in entry["cell"].split("-")])
    index_path.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    print(
        f"Converted {len(index)} cells in {time.perf_counter() - began:.1f} s, "
        f"{len(failures)} failed"
    )
    for cell_id, message in failures.items():
        print(f"  {cell_id}: {message}")


if __name__ == "__main__":
    main()
