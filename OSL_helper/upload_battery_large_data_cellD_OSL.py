"""Upload a **large** cycling dataset out-of-band and embed it in a test.

Cell D, Aging (A). Twin of ``upload_battery_large_data_OSL.py`` — see that file's
docstring for the full 5-step out-of-band (WikiFile + Distribution) rationale and
the ``ElectrochemicalTest.output`` coercion note. Only the cell, procedure,
source file and names differ here.

Run from the ``OSL_helper`` directory (paths are relative to it), after filling
in ``../examples/accounts.pwd.yaml``::

    python upload_battery_large_data_cellD_OSL.py

Requires the ``osl`` and ``maccor`` extras::

    pip install -e ".[osl,maccor]"
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from opensemantic.batteries import read_maccor
from opensemantic.batteries.v1 import ElectrochemicalTest, TestProcedureItem
from opensemantic.core.v1 import Label

from osw.defaults import params as default_params
from osw.defaults import paths as default_paths
from osw.express import OswExpress

default_paths.cred_filepath = Path(r"../examples/accounts.pwd.yaml")
default_params.wiki_domain = "wiki-dev.open-semantic-lab.org"
wiki_domain = "wiki-dev.open-semantic-lab.org"

osw_obj = OswExpress(domain=wiki_domain, cred_filepath=default_paths.cred_filepath)


dependencies = {
    "CyclingDataRow": "Category:OSW52787b16dd264707a2d2af4a3b866936",
    "ElectrochemicalCyclingDataset": "Category:OSW5af2a0c1f6a848b591678b2473674a49",
}

# Will run everytime the script is executed, uncomment if not yet installed
# osw_obj.install_dependencies(dependencies, mode="replace")

from osw.model.entity import (  # noqa: E402
    Distribution,
    ElectrochemicalCyclingDataset,
)

# ---------------------------------------------------------------------------
# Cell + procedure to attach to.
# ---------------------------------------------------------------------------

cell = "Item:OSWd71067591787464d9831576c1bba9518"  # cell_d

aging_test_a = "Item:OSW365966aaa8d64804b5ff0351c9db5382"  # Aging A

test_procedure = [
    TestProcedureItem(
        test_procedure_subcategory="Category:OSWdda41d4a4ec0421babe0295c6edcb5df",
        test_procedure_instance=aging_test_a,
        test_procedure_instance_property="Property:HasProcedure",
    )
]

# ---------------------------------------------------------------------------
# 1. Load a real Maccor export and reduce it to the bare ``data`` array
# ---------------------------------------------------------------------------

HERE = Path(__file__).resolve().parent
MACCOR_DIR = HERE.parent / "tests" / "data" / "cycling" / "maccor"
SOURCE = MACCOR_DIR / "raz-IDCyLIB-E1-full cell4_mims_client1_trimmed.txt"
DATASET_NAME = "Cell D - Aging (A) Dataset"
TEST_NAME = "Cell D - Aging (A)"


def _data_array() -> list:
    """Import the cycler file and return just the serialized ``data`` array.

    ``exclude_defaults`` keeps the array compact (values left at a
    characteristic's default unit are omitted and restored on load), exactly like
    ``examples/roundtrip_maccor_json.py`` — only here we keep the array alone,
    without the surrounding dataset envelope.
    """
    dataset = read_maccor(SOURCE, fmt="mims_client1")
    payload = dataset.to_json(exclude_defaults=True)
    array = payload["data"]
    print(f"Imported {len(array)} rows from {SOURCE.name}")
    return array


# ---------------------------------------------------------------------------
# 2./3. Upload the array as a WikiFile and build the lightweight dataset
# ---------------------------------------------------------------------------


def build_large_dataset() -> ElectrochemicalCyclingDataset:
    array = _data_array()

    # Write the array to a temp ``.json`` file for upload. The suffix drives the
    # uploaded WikiFile's suffix, so the download side gets a ``.json`` back.
    tmp_dir = Path(tempfile.mkdtemp(prefix="osw_large_ds_"))
    local_json = tmp_dir / "cycling_data.json"
    with local_json.open("w", encoding="utf-8") as fh:
        json.dump(array, fh, ensure_ascii=False)
    print(f"Wrote {local_json} ({local_json.stat().st_size} bytes)")

    # Upload as a standalone WikiFile. ``result`` is a WikiFileController, so its
    # ``.url`` is the full wiki file page URL — the direct link the dashboard's
    # ``download_file`` consumes (it parses the domain + title back out of it).
    result = osw_obj.upload_file(
        source=local_json,
        label=[Label(text=DATASET_NAME)],
        name=local_json.name,
    )
    download_url = result.url
    print(f"Uploaded WikiFile: {download_url}")

    # Lightweight dataset entity: no inline rows, only the Distribution pointer.
    return ElectrochemicalCyclingDataset(
        label=[Label(text=DATASET_NAME)],
        data=[],
        distributions=[Distribution(download_url=download_url)],
    )


# ---------------------------------------------------------------------------
# 4./5. Store the dataset, then embed it in an ElectrochemicalTest
# ---------------------------------------------------------------------------


def upload_large_test() -> ElectrochemicalTest:
    dataset = build_large_dataset()

    # Store the dataset on its own page FIRST: the entity layer keeps its
    # ``distributions`` (unlike the coerced copy inside the test's ``output``).
    osw_obj.store_entity(dataset)
    print(f"Stored dataset {dataset.get_iri()}")

    test = ElectrochemicalTest(
        label=[Label(text=TEST_NAME)],
        device_under_test=[cell],
        test_procedure=test_procedure,
        output=[dataset],
    )

    # Storing the test writes the HasOutput -> dataset link (by IRI), plus HasDut
    # and HasProcedure, so the dashboard's ``-HasOutput`` query finds the dataset.
    osw_obj.store_entity(test)
    print(f"Stored test {test.get_iri()}")
    return test


if __name__ == "__main__":
    upload_large_test()
