"""Upload a large cycling dataset out-of-band and embed it in a test.

A cycling dataset normally carries its rows inline in ``data``, which does not
scale once the whole array has to live in the entity's JSON slot. For large
datasets:

1. ``externalize_data`` writes the rows to a WikiFile, attaches it as a
   ``Distribution`` (media type, size, download URL) and clears ``data``,
2. the dataset is stored on its own page so the distribution persists,
3. an ``ElectrochemicalTest`` references it through ``output``, which creates the
   HasOutput / HasDut / HasProcedure links the dashboard queries.

The dashboard reverses step 1 with ``materialize_data``.

Typing note: v1 ``ElectrochemicalTest.output`` is typed ``List[Item]``, so what
it holds is read as an ``Item`` even though pydantic keeps the dataset's own
fields. Hence step 2: the dataset's own page carries the distribution and the
test references it by IRI, so nothing depends on how the nested copy is typed.

Nothing is written unless ``--store`` is passed. Run from the ``examples``
directory, after filling in ``accounts.pwd.yaml``::

    python upload_large_dataset_OSL.py --dataset cell_c
    python upload_large_dataset_OSL.py --dataset cell_d --store

Needs the optional extras::

    pip install -e ".[osl,maccor]"
"""

from __future__ import annotations

import argparse
from pathlib import Path

from osw.controller.file.wiki import WikiFileController
from osw.defaults import params as default_params
from osw.defaults import paths as default_paths
from osw.express import OswExpress

from opensemantic.batteries import read_maccor
from opensemantic.batteries.v1 import ElectrochemicalTest, TestProcedureItem
from opensemantic.core.v1 import Label

WIKI_DOMAIN = "wiki-dev.open-semantic-lab.org"
CRED_FILEPATH = Path("accounts.pwd.yaml")

MACCOR_DIR = (
    Path(__file__).resolve().parent.parent / "tests" / "data" / "cycling" / "maccor"
)

AGING_PROCEDURE = "Category:OSWdda41d4a4ec0421babe0295c6edcb5df"
AGING_TEST_A = "Item:OSW365966aaa8d64804b5ff0351c9db5382"
AGING_TEST_B = "Item:OSW606b66a2c1a94f8c86c3821807cf9bff"

# Cell, procedure and source file per dataset. The cells and procedures are the
# instances created by upload_example_data_OSL.py, so these land in the same tree.
DATASETS = {
    "cell_c": {
        "cell": "Item:OSW35ff60500092495ba72d0624f830129b",
        "procedure": AGING_TEST_B,
        "source": "raz-IDCyLIB-E1-full cell4_mims_client1_trimmed.txt",
        "name": "Cell C - Aging (B)",
    },
    "cell_d": {
        "cell": "Item:OSWd71067591787464d9831576c1bba9518",
        "procedure": AGING_TEST_A,
        "source": "raz-IDCyLIB-E1-full cell4_mims_client1_trimmed.txt",
        "name": "Cell D - Aging (A)",
    },
    "cell_e": {
        "cell": "Item:OSW74fa26bf50404bd7bdf4e770d61baa85",
        "procedure": AGING_TEST_A,
        "source": "raz-IDCyLIB-E1-full cell2_mims_client1_trimmed.txt",
        "name": "Cell E - Aging (A)",
    },
}


def build_dataset(osw_obj, spec: dict, fmt: str = "json"):
    """Import the cycler file and move its rows into a WikiFile.

    ``fmt`` picks the serialization: ``json`` keeps the typed values, ``csv``
    writes the pint-pandas frame with its unit header row.
    """
    source = MACCOR_DIR / spec["source"]
    dataset = read_maccor(source, fmt="mims_client1")
    print(f"Imported {len(dataset.data)} rows from {source.name}")

    dataset.label = [Label(text=f"{spec['name']} Dataset")]
    wiki_file = WikiFileController(
        label=dataset.label, name=f"cycling_data.{fmt}", osw=osw_obj
    )
    distribution = dataset.externalize_data(wiki_file, fmt=fmt)
    print(
        f"Uploaded WikiFile: {distribution.download_url} "
        f"({distribution.byte_size} bytes)"
    )
    return dataset


def upload(osw_obj, spec: dict, fmt: str = "json") -> ElectrochemicalTest:
    dataset = build_dataset(osw_obj, spec, fmt)

    test = ElectrochemicalTest(
        label=[Label(text=spec["name"])],
        device_under_test=[spec["cell"]],
        test_procedure=[
            TestProcedureItem(
                test_procedure_subcategory=AGING_PROCEDURE,
                test_procedure_instance=spec["procedure"],
                test_procedure_instance_property="Property:HasProcedure",
            )
        ],
        output=[dataset],
    )

    # The dataset goes first: its own page keeps the distribution.
    osw_obj.store_entity(dataset)
    print(f"Stored dataset {dataset.get_iri()}")
    osw_obj.store_entity(test)
    print(f"Stored test {test.get_iri()}")

    # Read the rows back out of the distribution, the way the dashboard does.
    rows = dataset.materialize_data()
    print(f"Round trip: {len(rows)} rows materialized back from the {fmt} file")
    return test


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dataset", choices=sorted(DATASETS), default="cell_c")
    parser.add_argument("--domain", default=WIKI_DOMAIN)
    parser.add_argument("--cred-filepath", type=Path, default=CRED_FILEPATH)
    parser.add_argument("--format", choices=["json", "csv"], default="json")
    parser.add_argument("--store", action="store_true", help="write to the wiki")
    args = parser.parse_args()

    spec = DATASETS[args.dataset]
    if not args.store:
        # The rows are uploaded as part of building the dataset, so a dry run
        # stops before touching the wiki.
        source = MACCOR_DIR / spec["source"]
        dataset = read_maccor(source, fmt="mims_client1")
        print(f"Imported {len(dataset.data)} rows from {source.name}")
        print("Dry run. Pass --store to upload the rows and write the test.")
        return

    default_paths.cred_filepath = args.cred_filepath
    default_params.wiki_domain = args.domain
    osw_obj = OswExpress(domain=args.domain, cred_filepath=args.cred_filepath)
    upload(osw_obj, spec, args.format)


if __name__ == "__main__":
    main()
