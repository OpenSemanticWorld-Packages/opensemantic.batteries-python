"""Upload one multi-stage discharge cell as a cycling dataset, test and entities.

The source is a folder of pickles, one per cell (``<batch>-<channel>.pkl``),
each holding ``{cell_id: {"rul": ..., "dq": ..., "data": {cycle: DataFrame}}}``
(see ``DATASET_FORMAT.md`` in that folder). Every cycle has six steps: a CC
charge, a CC-CV charge and four CC discharge stages. The script

1. loads the pickle with a restricted unpickler (pandas, numpy and ``slice``
   only),
2. parses the cycles into ``StepTypedCyclingDataRow`` rows with a continuous
   test time, cycle numbers from 0, ``step_type`` and a capacity that is reset
   only when the step type changes (the four discharge stages form one counter),
3. validates the rows and compares the per-cycle discharge capacity from
   ``cycle_summary`` with the source ``dq`` values,
4. builds a ``CylindricalCell``, an ``AgingTestProcedure`` (one per first
   discharge stage C-rate) and an ``ElectrochemicalTest``. The UUIDs of all
   entities, the dataset and its WikiFile are derived from fixed names, so a
   rerun overwrites the pages of that cell instead of creating duplicates, and
   other cells reuse the same procedure pages.

Cells in ``SKIPPED_CELLS`` are refused: their source data repeats timestamps
within a cycle, which breaks the strictly increasing test time.

Without ``--store`` nothing is written: the dataset is serialized and parsed
back offline and the time per phase and the peak memory are printed. With
``--store`` the entities are stored in the order cell, procedure, dataset
(rows externalized to a WikiFile as in ``upload_large_dataset_OSL.py``), test,
and the rows are read back with ``materialize_data``.

Run from the ``examples/dev`` directory, after filling in
``examples/accounts.pwd.yaml``::

    python upload_multistage_dataset_OSL.py --cell 4-3
    python upload_multistage_dataset_OSL.py --cell 4-3 --max-cycles 20
    python upload_multistage_dataset_OSL.py --cell 4-3 --store

Needs the optional extras::

    pip install -e ".[osl]"
"""

from __future__ import annotations

import argparse
import json
import pickle
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, List, Optional

import mwclient.errors
import numpy as np
import pandas as pd
from osw.controller.file.wiki import WikiFileController
from osw.core import model as osw_model
from osw.defaults import params as default_params
from osw.defaults import paths as default_paths
from osw.express import OswExpress

from opensemantic.base import _pint_csv
from opensemantic.base._dataset_controller_mixin import row_class_of
from opensemantic.batteries import (
    CyclingDatasetController,
    LinkedTestProcedureItem,
    ProcedureLinkedElectrochemicalTest,
    StepType,
    StepTypedCyclingDataRow,
    cycle_summary,
)
from opensemantic.batteries.v1 import (
    AgingTestProcedure,
    CylindricalCell,
)
from opensemantic.core.v1 import Description, Label

WIKI_DOMAIN = "wiki-dev.open-semantic-lab.org"
CRED_FILEPATH = Path(__file__).resolve().parent.parent / "accounts.pwd.yaml"
DATA_DIR = Path(r"C:\Users\feiler\Desktop\Example_Data_For_BatterieDigital")

AGING_PROCEDURE = "Category:OSW1b877eab05e442999835f2f595d1d1e7"

# Fixed namespace for the deterministic entity UUIDs. Never change it: the
# UUIDs of existing wiki pages derive from it.
NAMESPACE = uuid.UUID("6f1d3c0e-8a52-4b7e-9d41-2c5a7b9e0f13")

# Source data with repeated timestamps within a cycle (all in discharge_2):
# 4-5 cycle 341, 9-5 cycles 185 and 1188, 9-7 cycle 1323.
SKIPPED_CELLS = {"4-5", "9-5", "9-7"}

UPLOAD_CHUNK_SIZE = 2**31 - 1  # the dev wiki's maxuploadsize

NOMINAL_CAPACITY_AH = 1.1
GAP_SECONDS = 5.0  # added between the last point of a cycle and the next cycle

STATUS_TO_STEP = {
    "Constant current charge": StepType.CC_CHG,
    "Constant current-constant voltage charge": StepType.CCCV_CHG,
    "Constant current discharge_0": StepType.CC_DCH,
    "Constant current discharge_1": StepType.CC_DCH,
    "Constant current discharge_2": StepType.CC_DCH,
    "Constant current discharge_3": StepType.CC_DCH,
}
STATUS_ORDER = list(STATUS_TO_STEP)


class SafeUnpickler(pickle.Unpickler):
    """Allows only pandas, numpy and ``builtins.slice`` (see DATASET_FORMAT.md)."""

    def find_class(self, module, name):
        if module.startswith(("pandas.", "numpy")) or (
            module == "builtins" and name == "slice"
        ):
            return super().find_class(module, name)
        raise pickle.UnpicklingError(f"blocked {module}.{name}")


def load_cell(path: Path) -> dict:
    with open(path, "rb") as f:
        return SafeUnpickler(f).load()


class RerunWikiFileController(WikiFileController):
    """Accepts an upload that MediaWiki refuses as unchanged.

    A rerun uploads the same bytes under the same fixed file name, which
    MediaWiki answers with ``fileexists-no-change``. The file on the wiki is
    then already correct, so the upload counts as done. The file metadata is
    not stored in that case, so the caller stores it afterwards.
    """

    def put(self, file, **kwargs):
        try:
            super().put(file, **kwargs)
        except mwclient.errors.APIError as error:
            if error.code != "fileexists-no-change":
                raise
            print(f"File {self.title} is unchanged on the wiki, upload skipped")


@contextmanager
def phase(name: str, timings: Dict[str, float]):
    start = time.perf_counter()
    yield
    timings[name] = time.perf_counter() - start
    print(f"[{name}] {timings[name]:.1f} s")


def peak_memory_mb() -> Optional[float]:
    """Peak working set of this process in MB (Windows), else peak RSS."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = Counters()
        counters.cb = ctypes.sizeof(Counters)
        kernel = ctypes.windll.kernel32
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        ok = ctypes.windll.psapi.GetProcessMemoryInfo(
            wintypes.HANDLE(kernel.GetCurrentProcess()),
            ctypes.byref(counters),
            counters.cb,
        )
        return counters.PeakWorkingSetSize / 1e6 if ok else None
    try:
        import resource

        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e3
    except ImportError:
        return None


def build_frame(cycles: Dict[int, pd.DataFrame], max_cycles: Optional[int]):
    """Concatenate the cycles into one pint-pandas frame of row fields."""
    parts = []
    offset = 0.0
    for n, key in enumerate(sorted(cycles)):
        if max_cycles is not None and n >= max_cycles:
            break
        df = cycles[key]
        status = df["Status"].to_numpy()
        step_count = np.array([STATUS_ORDER.index(s) + 1 for s in status])
        step_type = np.array([STATUS_TO_STEP[s].value for s in status], dtype=object)
        source = df["Capacity (mAh)"].to_numpy(dtype=float)

        # segments: runs of equal step type; baseline is the source capacity of
        # the row before the segment (0 for the first segment of the cycle)
        start = np.ones(len(df), dtype=bool)
        start[1:] = step_type[1:] != step_type[:-1]
        previous = np.concatenate([[0.0], source[:-1]])
        baseline = pd.Series(np.where(start, previous, np.nan)).ffill().to_numpy()
        capacity = np.abs(source - baseline)

        time_s = df["Time (s)"].to_numpy(dtype=float)
        parts.append(
            pd.DataFrame(
                {
                    "test_time": time_s + offset,
                    "voltage": df["Voltage (V)"].to_numpy(dtype=float),
                    "current": df["Current (mA)"].to_numpy(dtype=float),
                    "capacity": capacity,
                    "cycle_count": np.full(len(df), n, dtype=float),
                    "step_count": step_count.astype(float),
                    "step_type": step_type,
                }
            )
        )
        offset += float(time_s[-1]) + GAP_SECONDS
    frame = pd.concat(parts, ignore_index=True)
    return pd.DataFrame(
        {
            "test_time": pd.Series(frame["test_time"], dtype="pint[second]"),
            "voltage": pd.Series(frame["voltage"], dtype="pint[volt]"),
            "current": pd.Series(frame["current"], dtype="pint[milliampere]"),
            "capacity": pd.Series(frame["capacity"], dtype="pint[milliampere_hour]"),
            "cycle_count": pd.Series(frame["cycle_count"], dtype="pint[dimensionless]"),
            "step_count": pd.Series(frame["step_count"], dtype="pint[dimensionless]"),
            "step_type": frame["step_type"],
        }
    )


def validate(rows: List[StepTypedCyclingDataRow]) -> None:
    """Raise ``ValueError`` on the first violated rule."""
    required = [
        "voltage",
        "current",
        "test_time",
        "capacity",
        "cycle_count",
        "step_type",
    ]
    if not rows:
        raise ValueError("no rows")
    previous = None
    segment_last_capacity = None  # capacity of the last row of the prior segment
    for i, row in enumerate(rows):
        for field in required:
            value = getattr(row, field, None)
            if value is None or (hasattr(value, "value") and value.value is None):
                raise ValueError(f"row {i}: missing {field}")
        capacity = row.capacity.to_pint().to("milliampere_hour").magnitude
        if capacity < 0:
            raise ValueError(f"row {i}: negative capacity {capacity}")
        time_s = row.test_time.to_pint().to("second").magnitude
        cycle = row.cycle_count.value
        if previous is None:
            if cycle != 0:
                raise ValueError(f"first cycle_count is {cycle}, expected 0")
        else:
            p_time, p_cycle, p_step, p_capacity = previous
            if time_s <= p_time:
                raise ValueError(f"row {i}: test_time {time_s} <= {p_time}")
            if cycle not in (p_cycle, p_cycle + 1):
                raise ValueError(f"row {i}: cycle {cycle} after {p_cycle}")
            if row.step_type != p_step:
                segment_last_capacity = p_capacity
                if not (capacity < segment_last_capacity or capacity < 1.0):
                    raise ValueError(
                        f"row {i}: capacity {capacity} at a new segment is not "
                        f"below {segment_last_capacity} and not near 0"
                    )
        previous = (time_s, cycle, row.step_type, capacity)


def check_dq(
    rows, dq: Dict[int, float], max_cycles: Optional[int], partial: bool = False
) -> float:
    """Compare ``cycle_summary`` discharge capacity (C -> mAh) with source dq.

    With ``partial`` a cycle without discharge (cut off in a shortened CSV) is
    skipped and reported instead of raising.
    """
    summary = cycle_summary(rows)
    diffs = {}
    skipped = []
    for row in summary.data:
        cycle = int(row.cycle_count.value)
        if row.discharge_capacity is None:
            if partial:
                skipped.append(cycle)
                continue
            raise ValueError(f"cycle {cycle}: no discharge capacity in summary")
        mah = row.discharge_capacity.value / 3.6  # 1 mAh = 3.6 C
        diffs[cycle] = abs(mah - float(dq[cycle + 1]))
    first, last = min(diffs), max(diffs)
    print(
        f"dq check: cycles {first}..{last}, diff first={diffs[first]:.3e} mAh, "
        f"last={diffs[last]:.3e} mAh, max={max(diffs.values()):.3e} mAh"
    )
    if skipped:
        print(f"dq check: skipped cycles without discharge: {skipped}")
    return max(diffs.values())


def first_stage_c_rate(cycles: Dict[int, pd.DataFrame]) -> int:
    """C-rate of the first discharge stage from the first cycle's median current."""
    df = cycles[min(cycles)]
    current = df.loc[df["Status"] == "Constant current discharge_0", "Current (mA)"]
    amps = abs(float(current.median())) / 1000.0
    c_rate = round(amps / NOMINAL_CAPACITY_AH)
    if c_rate < 1:
        raise ValueError(f"unexpected first stage current {amps} A")
    return int(c_rate)


def build_entities(cell_id: str, c_rate: int):
    cell = CylindricalCell(
        uuid=uuid.uuid5(NAMESPACE, f"cell:{cell_id}"),
        label=[Label(text=f"Cell {cell_id}")],
    )
    amps = c_rate * NOMINAL_CAPACITY_AH
    name = f"Multi-stage discharge aging, {c_rate}C first stage"
    procedure = AgingTestProcedure(
        uuid=uuid.uuid5(NAMESPACE, f"procedure:{c_rate}C"),
        label=[Label(text=name)],
        description=[
            Description(
                text=(
                    "5C CC charge, 1C CC-CV charge to 3.6 V, then four CC "
                    f"discharge stages: the first at {c_rate}C ({amps:.1f} A), "
                    "the last at 2C down to about 1.95 V."
                )
            )
        ],
    )
    return cell, procedure, name


def build_test(
    cell, procedure, name, cell_id, dataset
) -> ProcedureLinkedElectrochemicalTest:
    return ProcedureLinkedElectrochemicalTest(
        uuid=uuid.uuid5(NAMESPACE, f"test:{cell_id}"),
        label=[Label(text=f"Cell {cell_id} - {name}")],
        device_under_test=[cell.get_iri()],
        test_procedure=[
            LinkedTestProcedureItem(
                test_procedure_subcategory=AGING_PROCEDURE,
                test_procedure_instance=procedure.get_iri(),
                test_procedure_instance_property="Property:HasProcedure",
            )
        ],
        output=[dataset],
    )


def parse_back(payload: bytes, dataset_cls, fmt: str, label) -> int:
    """Parse serialized rows the way ``materialize_data`` does; return the count."""
    if fmt == "csv":
        return len(dataset_cls.from_df(_pint_csv.read_csv(payload), label=label).data)
    row_cls = row_class_of(dataset_cls)
    entries = json.loads(payload.decode("utf-8"))
    if isinstance(entries, dict):
        entries = entries.get("data", [])
    return len([row_cls(**entry) for entry in entries])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--cell", default="4-3", help="cell id, e.g. 4-3")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--max-cycles", type=int, default=None)
    parser.add_argument(
        "--csv",
        type=Path,
        default=None,
        help="take the rows from this CSV (e.g. a shortened csv_osl file) "
        "instead of the pickle",
    )
    parser.add_argument("--domain", default=WIKI_DOMAIN)
    parser.add_argument("--cred-filepath", type=Path, default=CRED_FILEPATH)
    parser.add_argument("--format", choices=["json", "csv"], default="json")
    parser.add_argument("--store", action="store_true", help="write to the wiki")
    args = parser.parse_args()

    timings: Dict[str, float] = {}
    cell_id = args.cell
    if cell_id in SKIPPED_CELLS:
        raise SystemExit(f"Cell {cell_id} is skipped: repeated timestamps")

    with phase("load", timings):
        path = args.data_dir / f"{cell_id}.pkl"
        content = load_cell(path)[cell_id]
        cycles = content["data"]
        print(f"Loaded {path.name}: {len(cycles)} cycles")

    c_rate = first_stage_c_rate(cycles)
    cell, procedure, procedure_name = build_entities(cell_id, c_rate)
    print(f"First discharge stage: {c_rate}C -> {procedure_name}")

    with phase("frame", timings):
        if args.csv is not None:
            # the pickle still gives the C-rate and dq; the rows come from the CSV
            frame = _pint_csv.read_csv(args.csv.read_bytes())
            print(f"Read {len(frame)} rows from {args.csv}")
        else:
            frame = build_frame(cycles, args.max_cycles)

    with phase("rows", timings):
        dataset = CyclingDatasetController.from_df(
            frame,
            uuid=uuid.uuid5(NAMESPACE, f"dataset:{cell_id}"),
            label=f"Cell {cell_id} - {procedure_name} Dataset",
        )
        rows = dataset.data
        print(f"Built {len(rows)} rows")
    del frame

    with phase("validate", timings):
        validate(rows)
        print("Validation passed")
        check_dq(rows, content["dq"], args.max_cycles, partial=args.csv is not None)

    print(f"Cell {cell.get_iri()}, procedure {procedure.get_iri()}")

    if not args.store:
        test = build_test(cell, procedure, procedure_name, cell_id, dataset)
        print(f"Test label: {test.label[0].text}")
        with phase("serialize", timings):
            payload = dataset.serialize_data(args.format)
            print(f"Serialized {args.format}: {len(payload) / 1e6:.1f} MB")
        with phase("parse back", timings):
            count = parse_back(payload, type(dataset), args.format, dataset.label)
            print(f"Parsed back {count} rows (expected {len(rows)})")
            if count != len(rows):
                raise SystemExit("row count mismatch after parse back")
        peak = peak_memory_mb()
        print(f"Peak memory: {peak:.0f} MB" if peak else "Peak memory: unknown")
        print(f"Total: {sum(timings.values()):.1f} s")
        print("Dry run. Pass --store to upload the rows and write the entities.")
        return

    default_paths.cred_filepath = args.cred_filepath
    default_params.wiki_domain = args.domain
    osw_obj = OswExpress(domain=args.domain, cred_filepath=args.cred_filepath)
    # mwclient uploads files above chunk_size in chunks and then returns the
    # response wrapped in {"upload": ...}, which osw's upload check rejects
    # although the upload succeeded. One request avoids the chunked path.
    osw_obj.mw_site.chunk_size = UPLOAD_CHUNK_SIZE

    def store(entity, namespace=None):
        """Store ``entity``, replacing a page from an earlier run."""
        param = {"namespace": namespace} if namespace else {}
        osw_obj.store_entity(
            OswExpress.StoreEntityParam(entities=[entity], overwrite=True, **param)
        )

    store(cell)
    print(f"Stored cell {cell.get_iri()}")
    store(procedure)
    print(f"Stored procedure {procedure.get_iri()}")

    wiki_file = RerunWikiFileController(
        uuid=uuid.uuid5(NAMESPACE, f"file:{cell_id}:{args.format}"),
        label=dataset.label,
        name=f"cycling_data.{args.format}",
        osw=osw_obj,
    )
    distribution = dataset.externalize_data(wiki_file, fmt=args.format)
    # externalize_data passes no store options to the upload, which keeps an
    # existing file page unchanged, so write the file metadata again
    store(wiki_file.cast(osw_model.WikiFile), namespace=wiki_file.namespace)
    print(
        f"Uploaded WikiFile: {distribution.download_url} "
        f"({distribution.byte_size} bytes)"
    )
    store(dataset)
    print(f"Stored dataset {dataset.get_iri()}")
    # built after externalize_data, so the embedded dataset carries no rows
    test = build_test(cell, procedure, procedure_name, cell_id, dataset)
    store(test)
    print(f"Stored test {test.get_iri()}")

    rows_back = dataset.materialize_data()
    print(f"Round trip: {len(rows_back)} rows materialized back")


if __name__ == "__main__":
    main()
