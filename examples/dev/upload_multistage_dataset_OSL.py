"""Upload one multi-stage discharge cell as a cycling dataset, test and entities.

The source is a folder of pickles, one per cell (``<batch>-<channel>.pkl``),
each holding ``{cell_id: {"rul": ..., "dq": ..., "data": {cycle: DataFrame}}}``
(see ``DATASET_FORMAT.md`` in that folder). Every cycle has six steps: a CC
charge, a CC-CV charge and four CC discharge stages. The script

1. loads the pickle with a restricted unpickler (pandas, numpy and ``slice``
   only),
2. parses the cycles into a frame of ``CyclingDataRow`` fields with a
   continuous test time, cycle numbers from 0, ``step_type`` and a capacity
   that is reset only when the step type changes (the four discharge stages
   form one counter),
3. validates the frame column-wise and compares the per-cycle discharge
   capacity from ``cycle_summary`` with the source ``dq`` values,
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
and the file is read back with ``materialize_df`` to compare the row count.

With ``--csv`` and ``--format csv`` the CSV file is uploaded as it is. Only a
sample of rows is built as ``CyclingDataRow``: the first ``SAMPLE_ROWS`` rows,
which must serialize to the same bytes as the start of the file, and
``SAMPLE_ROWS`` rows spread over the whole file. Building every row takes
about two minutes per million rows and only reproduces the file.

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
from typing import Dict, Optional

import mwclient.errors
import numpy as np
import pandas as pd
from osw.controller.file.wiki import WikiFileController
from osw.core import model as osw_model
from osw.defaults import params as default_params
from osw.defaults import paths as default_paths
from osw.express import OswExpress

from opensemantic.base import _pint_csv
from opensemantic.base._dataset_controller_mixin import (
    CSV_MEDIA_TYPE,
    _field_type,
    _file_url,
    _NamedBytesIO,
    row_class_of,
)
from opensemantic.batteries import CyclingDatasetController, StepType, cycle_summary
from opensemantic.batteries.v1 import (
    AgingTestProcedure,
    CylindricalCell,
    ElectrochemicalTest,
    TestProcedureItem,
)
from opensemantic.core import get_file_controller
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

# rows built as CyclingDataRow when a --csv file is uploaded as it is
SAMPLE_ROWS = 10_000

# Added to the description of every stored entity. The data is licensed under
# CC BY 4.0, which asks for attribution and a note on the changes.
SOURCE_TEXT = (
    "Source: Ye Yuan, Guijun Ma, Songpei Xu (2022). The Dataset for: Real-time "
    "personalized health status prediction of lithium-ion batteries using deep "
    "transfer learning. Mendeley Data, version 2. "
    "https://doi.org/10.17632/nsc7hnsg4s.2, license CC BY 4.0. "
    "Original publication: G. Ma, S. Xu, B. Jiang, C. Cheng, X. Yang, Y. Shen, "
    "T. Yang, Y. Huang, H. Ding, Y. Yuan. Real-time personalized health status "
    "prediction of lithium-ion batteries using deep transfer learning. Energy "
    "Environ. Sci. 15 (2022) 4083-4094. https://doi.org/10.1039/d2ee01676a. "
    "Changes: converted from the original pickle files into CyclingDataRow "
    "fields with a continuous test time, cycle numbers from 0, a step type and "
    "a capacity that restarts when the step type changes."
)


def source_description() -> Description:
    return Description(text=SOURCE_TEXT, lang="en")


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


def validate(frame: pd.DataFrame) -> None:
    """Raise ``ValueError`` on a violated rule, checked column-wise.

    The message names the first row of the first rule that fails. Rows are
    counted from 0 in file order.
    """
    required = [
        "voltage",
        "current",
        "test_time",
        "capacity",
        "cycle_count",
        "step_type",
    ]
    if frame.empty:
        raise ValueError("no rows")
    for field in required:
        if field not in frame.columns:
            raise ValueError(f"missing column {field}")
        missing = np.flatnonzero(frame[field].isna().to_numpy())
        if missing.size:
            raise ValueError(f"row {missing[0]}: missing {field}")

    def magnitudes(field: str, unit: str) -> np.ndarray:
        return frame[field].pint.to(unit).pint.magnitude.to_numpy(dtype=float)

    def first(mask: np.ndarray, message) -> None:
        hits = np.flatnonzero(mask)
        if hits.size:
            raise ValueError(message(hits[0]))

    capacity = magnitudes("capacity", "milliampere_hour")
    time_s = magnitudes("test_time", "second")
    cycle = magnitudes("cycle_count", "dimensionless")
    step = frame["step_type"].to_numpy()

    first(capacity < 0, lambda i: f"row {i}: negative capacity {capacity[i]}")
    if cycle[0] != 0:
        raise ValueError(f"first cycle_count is {cycle[0]}, expected 0")
    # rule for row i + 1 against its predecessor i
    first(
        np.diff(time_s) <= 0,
        lambda i: f"row {i + 1}: test_time {time_s[i + 1]} <= {time_s[i]}",
    )
    cycle_step = np.diff(cycle)
    first(
        (cycle_step != 0) & (cycle_step != 1),
        lambda i: f"row {i + 1}: cycle {cycle[i + 1]} after {cycle[i]}",
    )
    # at a new segment the capacity starts below the last capacity of the
    # prior segment or near 0
    new_segment = step[1:] != step[:-1]
    reset = (capacity[1:] < capacity[:-1]) | (capacity[1:] < 1.0)
    first(
        new_segment & ~reset,
        lambda i: (
            f"row {i + 1}: capacity {capacity[i + 1]} at a new segment is not "
            f"below {capacity[i]} and not near 0"
        ),
    )


def check_sample(frame: pd.DataFrame, raw: bytes, label: str) -> None:
    """Build a sample of rows and check that ``raw`` is their CSV format.

    The first ``SAMPLE_ROWS`` rows go through ``CyclingDataRow`` and are
    serialized again; the result must be the same bytes as the start of
    ``raw``, the CSV file that is uploaded as it is. Another ``SAMPLE_ROWS``
    rows spread over the whole frame go through ``CyclingDataRow`` as well.
    """
    head = CyclingDatasetController.from_df(frame.iloc[:SAMPLE_ROWS], label=label)
    # two header lines (field, unit), then one line per row
    lines = raw.split(b"\n", len(head.data) + 2)
    expected = b"\n".join(lines[: len(head.data) + 2]) + b"\n"
    if head.serialize_data("csv") != expected:
        raise ValueError(
            f"the first {len(head.data)} rows of the CSV file do not serialize "
            "to the same bytes; run without --format csv to rebuild the file"
        )
    step = max(1, len(frame) // SAMPLE_ROWS)
    spread = CyclingDatasetController.from_df(frame.iloc[::step], label=label)
    print(
        f"Sample check passed: first {len(head.data)} rows serialize to the same "
        f"bytes, {len(spread.data)} rows (every {step}th) build as CyclingDataRow"
    )


def upload_bytes(dataset, file, payload: bytes, fmt: str):
    """``externalize_data`` for rows that are already serialized.

    ``externalize_data`` serializes ``dataset.data`` before the upload. A CSV
    file that ``check_sample`` accepted is that serialization already, so it
    is uploaded as it is. Attaches the distribution and clears ``data``.
    """
    controller = get_file_controller(file)
    name = getattr(controller, "name", None) or f"data.{fmt}"
    if not str(name).endswith(f".{fmt}"):
        name = f"{name}.{fmt}"
    controller.put(_NamedBytesIO(payload, str(name)))
    distribution_cls = _field_type(type(dataset), "distributions")
    distribution = distribution_cls(
        file=controller,
        media_type=CSV_MEDIA_TYPE,
        byte_size=len(payload),
        download_url=_file_url(controller),
    )
    dataset.distributions = list(dataset.distributions or []) + [distribution]
    dataset.data = None
    return distribution


def check_dq(
    data, dq: Dict[int, float], max_cycles: Optional[int], partial: bool = False
) -> float:
    """Compare ``cycle_summary`` discharge capacity (C -> mAh) with source dq.

    ``data`` is the frame or the rows; ``cycle_summary`` reads either.
    With ``partial`` a cycle without discharge (cut off in a shortened CSV) is
    skipped and reported instead of raising.
    """
    summary = cycle_summary(data)
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
        description=[source_description()],
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
            ),
            source_description(),
        ],
    )
    return cell, procedure, name


def build_test(cell, procedure, name, cell_id, dataset) -> ElectrochemicalTest:
    return ElectrochemicalTest(
        uuid=uuid.uuid5(NAMESPACE, f"test:{cell_id}"),
        label=[Label(text=f"Cell {cell_id} - {name}")],
        description=[source_description()],
        device_under_test=[cell.get_iri()],
        test_procedure=[
            TestProcedureItem(
                test_procedure_subcategory=AGING_PROCEDURE,
                test_procedure_instance=procedure.get_iri(),
                test_procedure_instance_property="Property:HasProcedure",
            )
        ],
        output=[dataset],
    )


def parse_back(payload: bytes, dataset_cls, fmt: str, label) -> int:
    """Parse serialized rows the way ``materialize_df`` does; return the count."""
    if fmt == "csv":
        return len(_pint_csv.read_csv(payload))
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

    raw = None
    with phase("frame", timings):
        if args.csv is not None:
            # the pickle still gives the C-rate and dq; the rows come from the CSV
            raw = args.csv.read_bytes()
            frame = _pint_csv.read_csv(raw)
            print(f"Read {len(frame)} rows from {args.csv}")
        else:
            frame = build_frame(cycles, args.max_cycles)
    row_count = len(frame)

    with phase("validate", timings):
        validate(frame)
        print("Validation passed")
        check_dq(frame, content["dq"], args.max_cycles, partial=args.csv is not None)

    dataset_uuid = uuid.uuid5(NAMESPACE, f"dataset:{cell_id}")
    dataset_label = f"Cell {cell_id} - {procedure_name} Dataset"
    # the CSV file is uploaded as it is; JSON and the pickle need every row
    payload = raw if raw is not None and args.format == "csv" else None
    with phase("rows", timings):
        if payload is not None:
            check_sample(frame, payload, dataset_label)
            dataset = CyclingDatasetController.from_df(
                frame.iloc[:0],
                uuid=dataset_uuid,
                label=dataset_label,
                description=[source_description()],
            )
        else:
            dataset = CyclingDatasetController.from_df(
                frame,
                uuid=dataset_uuid,
                label=dataset_label,
                description=[source_description()],
            )
            print(f"Built {len(dataset.data)} rows")
    del frame

    print(f"Cell {cell.get_iri()}, procedure {procedure.get_iri()}")

    if not args.store:
        test = build_test(cell, procedure, procedure_name, cell_id, dataset)
        print(f"Test label: {test.label[0].text}")
        if payload is None:
            with phase("serialize", timings):
                payload = dataset.serialize_data(args.format)
        print(f"Payload {args.format}: {len(payload) / 1e6:.1f} MB")
        with phase("parse back", timings):
            count = parse_back(payload, type(dataset), args.format, dataset.label)
            print(f"Parsed back {count} rows (expected {row_count})")
            if count != row_count:
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
        description=[source_description()],
        name=f"cycling_data.{args.format}",
        osw=osw_obj,
    )
    if payload is not None:
        distribution = upload_bytes(dataset, wiki_file, payload, args.format)
    else:
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

    count = len(dataset.materialize_df())
    print(f"Round trip: {count} rows read back (expected {row_count})")
    if count != row_count:
        raise SystemExit("row count mismatch after the round trip")


if __name__ == "__main__":
    main()
