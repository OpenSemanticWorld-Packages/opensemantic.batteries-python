"""Upload the example battery tree to an OSL wiki.

Creates one ``ElectrochemicalTest`` per cell and procedure, each with a small
synthetic cycling dataset inline. Together they form the tree the dashboard
examples browse: three cells, three procedures, six tests.

Cells and procedures are referenced by IRI; they are expected to exist on the
wiki already. For a large dataset stored out-of-band in a file, see
``upload_large_dataset_OSL.py``.

Nothing is written unless ``--store`` is passed. Run from the ``examples``
directory, after filling in ``accounts.pwd.yaml``::

    python upload_example_data_OSL.py
    python upload_example_data_OSL.py --store

Needs the optional ``osl`` extra::

    pip install -e ".[osl]"
"""

from __future__ import annotations

import argparse
import random
from pathlib import Path
from typing import List

from osw.defaults import params as default_params
from osw.defaults import paths as default_paths
from osw.express import OswExpress

from opensemantic.batteries import CyclingDatasetController
from opensemantic.batteries.v1 import (
    CyclingDataRow,
    ElectrochemicalTest,
    TestProcedureItem,
)
from opensemantic.characteristics.quantitative.v1 import (
    Count,
    ElectricCharge,
    ElectricCurrent,
    Time,
    Voltage,
)
from opensemantic.core.v1 import Label

WIKI_DOMAIN = "wiki-dev.open-semantic-lab.org"
CRED_FILEPATH = Path("accounts.pwd.yaml")

CELLS = {
    "cell_a": "Item:OSW7bae5d74c11842fc8fdc5f12d264a5f1",
    "cell_b": "Item:OSW4a20efb16be64868ab9d16a97838434a",
    "cell_c": "Item:OSW35ff60500092495ba72d0624f830129b",
}

PROCEDURE_SUBCATEGORY = "Category:OSWdda41d4a4ec0421babe0295c6edcb5df"
PROCEDURES = {
    "aging_a": "Item:OSW365966aaa8d64804b5ff0351c9db5382",
    "aging_b": "Item:OSW606b66a2c1a94f8c86c3821807cf9bff",
    "formation": "Item:OSWecce41274e5b403a9de4179b04b49a1e",
}

# (cell, procedure, label) per test run.
TESTS = [
    ("cell_a", "aging_a", "Cell A - Aging (A)"),
    ("cell_a", "formation", "Cell A - Formation"),
    ("cell_b", "aging_a", "Cell B - Aging (A)"),
    ("cell_b", "aging_b", "Cell B - Aging (B)"),
    ("cell_b", "formation", "Cell B - Formation"),
    ("cell_c", "formation", "Cell C - Formation"),
]


def sample_cycling_data() -> List[CyclingDataRow]:
    """Five rows of a toy charge profile spanning two cycles.

    ``cycle_count`` rolls over from 0 to 1 at the middle row, and ``capacity``
    accumulates the charge passed (Q += I*dt) within each cycle, resetting to
    zero when the new cycle starts.
    """
    base = [
        (0.0, 3.0, 0.0),
        (1.0, 3.1, 0.5),
        (2.0, 3.2, 0.5),
        (3.0, 3.3, 0.5),
        (4.0, 3.1, 0.0),
    ]
    mid = len(base) // 2

    rows: List[CyclingDataRow] = []
    capacity = 0.0
    prev_cycle = 0
    prev_time = 0.0
    for i, (t, v, c) in enumerate(base):
        cycle = 0 if i < mid else 1
        if cycle != prev_cycle:
            capacity = 0.0
            prev_cycle = cycle
        current = c + random.uniform(-0.1, 0.1)
        capacity += max(current, 0.0) * (t - prev_time)
        prev_time = t
        rows.append(
            CyclingDataRow(
                test_time=Time(value=t),
                voltage=Voltage(value=v + random.uniform(-0.3, 0.3)),
                current=ElectricCurrent(value=current),
                cycle_count=Count(value=cycle),
                capacity=ElectricCharge(value=round(capacity, 4)),
            )
        )
    return rows


def build_test(cell: str, procedure: str, label: str) -> ElectrochemicalTest:
    """One test run with its dataset inline.

    ``TestProcedureItem.test_procedure_instance`` takes an OSW IRI, not the
    procedure object; the dashboard resolves it back through its
    ``procedure_objects`` map.
    """
    dataset = CyclingDatasetController(
        label=[Label(text=f"{label} Dataset")],
        data=sample_cycling_data(),
    )
    return ElectrochemicalTest(
        label=[Label(text=label)],
        device_under_test=[CELLS[cell]],
        test_procedure=[
            TestProcedureItem(
                test_procedure_subcategory=PROCEDURE_SUBCATEGORY,
                test_procedure_instance=PROCEDURES[procedure],
                test_procedure_instance_property="Property:HasProcedure",
            )
        ],
        output=[dataset],
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--domain", default=WIKI_DOMAIN)
    parser.add_argument("--cred-filepath", type=Path, default=CRED_FILEPATH)
    parser.add_argument("--store", action="store_true", help="write to the wiki")
    args = parser.parse_args()

    osw_obj = None
    if args.store:
        default_paths.cred_filepath = args.cred_filepath
        default_params.wiki_domain = args.domain
        osw_obj = OswExpress(domain=args.domain, cred_filepath=args.cred_filepath)

    for cell, procedure, label in TESTS:
        test = build_test(cell, procedure, label)
        rows = len(test.output[0].data)
        if osw_obj is None:
            print(f"{label}: built with {rows} rows")
            continue
        # The dataset goes first so its own page keeps all fields; the test's
        # output only needs to reference it by IRI.
        osw_obj.store_entity(test.output[0])
        osw_obj.store_entity(test)
        print(f"{label}: stored {test.get_iri()}")

    if osw_obj is None:
        print("Dry run. Pass --store to write to the wiki.")


if __name__ == "__main__":
    main()
