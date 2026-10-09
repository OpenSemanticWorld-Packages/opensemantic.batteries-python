"""Upload a **large** cycling dataset out-of-band and embed it in a test.

Cell E, Aging (A). Twin of ``upload_battery_large_data_OSL.py`` — see that file's
docstring for the full 5-step out-of-band (WikiFile + Distribution) rationale and
the ``ElectrochemicalTest.output`` coercion note. Only the cell, procedure,
source file and names differ here.

Run from the ``OSL_helper`` directory (paths are relative to it), after filling
in ``../examples/accounts.pwd.yaml``::

    python upload_battery_large_data_cellE_OSL.py

Requires the ``osl`` and ``maccor`` extras::

    pip install -e ".[osl,maccor]"
"""

from __future__ import annotations

from pathlib import Path

from opensemantic.batteries import read_maccor
from opensemantic.batteries.v1 import ElectrochemicalTest, TestProcedureItem
from opensemantic.core.v1 import Label

from osw.controller.file.wiki import WikiFileController
from osw.defaults import params as default_params
from osw.defaults import paths as default_paths
from osw.express import OswExpress

default_paths.cred_filepath = Path(r"../examples/accounts.pwd.yaml")
default_params.wiki_domain = "wiki-dev.open-semantic-lab.org"
wiki_domain = "wiki-dev.open-semantic-lab.org"

osw_obj = OswExpress(domain=wiki_domain, cred_filepath=default_paths.cred_filepath)


# ---------------------------------------------------------------------------
# Cell + procedure to attach to.
# ---------------------------------------------------------------------------

cell = "Item:OSW74fa26bf50404bd7bdf4e770d61baa85"  # cell_e

aging_test_a = "Item:OSW365966aaa8d64804b5ff0351c9db5382"  # Aging A

test_procedure = [
    TestProcedureItem(
        test_procedure_subcategory="Category:OSWdda41d4a4ec0421babe0295c6edcb5df",
        test_procedure_instance=aging_test_a,
        test_procedure_instance_property="Property:HasProcedure",
    )
]

# ---------------------------------------------------------------------------
# 1. Load a real Maccor export and move its rows into a WikiFile
# ---------------------------------------------------------------------------

HERE = Path(__file__).resolve().parent
MACCOR_DIR = HERE.parent / "tests" / "data" / "cycling" / "maccor"
SOURCE = MACCOR_DIR / "raz-IDCyLIB-E1-full cell2_mims_client1_trimmed.txt"
DATASET_NAME = "Cell E - Aging (A) Dataset"
TEST_NAME = "Cell E - Aging (A)"


def build_large_dataset():
    """Import the cycler file and move its rows into a WikiFile.

    ``externalize_data`` serializes the rows, uploads them through the wiki file
    controller, attaches the file as a ``Distribution`` (media type, size and
    download URL) and clears the inline ``data``. The dashboard reverses it with
    ``materialize_data``.
    """
    dataset = read_maccor(SOURCE, fmt="mims_client1")
    print(f"Imported {len(dataset.data)} rows from {SOURCE.name}")

    wiki_file = WikiFileController(
        label=[Label(text=DATASET_NAME)], name="cycling_data.json", osw=osw_obj
    )
    distribution = dataset.externalize_data(wiki_file, fmt="json")
    print(
        f"Uploaded WikiFile: {distribution.download_url} "
        f"({distribution.byte_size} bytes)"
    )

    dataset.label = [Label(text=DATASET_NAME)]
    return dataset


# ---------------------------------------------------------------------------
# 2./3. Store the dataset, then embed it in an ElectrochemicalTest
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
