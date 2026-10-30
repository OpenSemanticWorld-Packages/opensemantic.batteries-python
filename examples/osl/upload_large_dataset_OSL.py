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

Each entry in ``DATASETS`` names the wiki pages of its test and dataset, and
the script writes to exactly those pages (``overwrite=True``), so a rerun
replaces them in place instead of creating duplicates. The test uses the
temporary ``ProcedureLinkedElectrochemicalTest``, which keeps the
``HasProcedure`` link that the generated ``TestProcedureItem`` drops.

Typing note: v1 ``ElectrochemicalTest.output`` is typed ``List[Item]``, so what
it holds is read as an ``Item`` even though pydantic keeps the dataset's own
fields. Hence step 2: the dataset's own page carries the distribution and the
test references it by IRI, so nothing depends on how the nested copy is typed.

Nothing is written unless ``--store`` is passed. Run from any
directory, after filling in ``examples/accounts.pwd.yaml``::

    python examples/osl/upload_large_dataset_OSL.py --dataset cell_a
    python examples/osl/upload_large_dataset_OSL.py --dataset cell_d --store

Needs the optional extras::

    pip install -e ".[osl,maccor]"
"""

from __future__ import annotations

import argparse
import uuid
from pathlib import Path

import mwclient.errors
from osw.controller.file.wiki import WikiFileController
from osw.core import model as osw_model
from osw.defaults import params as default_params
from osw.defaults import paths as default_paths
from osw.express import OswExpress

from opensemantic.batteries import (
    LinkedTestProcedureItem,
    ProcedureLinkedElectrochemicalTest,
    read_maccor,
)
from opensemantic.core.v1 import Label

WIKI_DOMAIN = "wiki-dev.open-semantic-lab.org"
CRED_FILEPATH = Path(__file__).resolve().parent.parent / "accounts.pwd.yaml"

MACCOR_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "tests"
    / "data"
    / "cycling"
    / "maccor"
)

AGING_PROCEDURE = "Category:OSWdda41d4a4ec0421babe0295c6edcb5df"
AGING_TEST_A = "Item:OSW365966aaa8d64804b5ff0351c9db5382"

# Fixed namespace for the WikiFile UUIDs. Never change it: the file pages of
# earlier runs derive from it.
NAMESPACE = uuid.UUID("3b0c6f6e-5a7d-4c1e-9f2b-8d4a1e7c2b90")

UPLOAD_CHUNK_SIZE = 2**31 - 1  # the dev wiki's maxuploadsize

# Cell, procedure, source file, and the test and dataset pages per entry. The
# cells and procedures are the instances created by upload_example_data_OSL.py,
# so these land in the same tree. The test and dataset pages are the ones on
# the dev wiki, so a rerun replaces them.
DATASETS = {
    "cell_a": {
        "cell": "Item:OSW7bae5d74c11842fc8fdc5f12d264a5f1",
        "procedure": AGING_TEST_A,
        "source": "raz-IDCyLIB-E1-full cell2_mims_client1_trimmed.txt",
        "name": "Cell A - Aging (A)",
        "test": "Item:OSWceecefe6587a49448422b78e225907d0",
        "dataset": "Item:OSW0c935e2a0fb84c6fbd7dcff8d20382eb",
    },
    "cell_c": {
        "cell": "Item:OSW35ff60500092495ba72d0624f830129b",
        "procedure": AGING_TEST_A,
        "source": "raz-IDCyLIB-E1-full cell2_mims_client1_trimmed.txt",
        "name": "Cell C - Aging (A)",
        "test": "Item:OSWc448310beb5e4d289910f00c68504ec2",
        "dataset": "Item:OSW4331674d84894bcb833203a726cb318c",
    },
    "cell_d": {
        "cell": "Item:OSWd71067591787464d9831576c1bba9518",
        "procedure": AGING_TEST_A,
        "source": "raz-IDCyLIB-E1-full cell4_mims_client1_trimmed.txt",
        "name": "Cell D - Aging (A)",
        "test": "Item:OSW3eaf8b64ee054bf087f4de94c0f4c0f8",
        "dataset": "Item:OSW459679e86d564535bed62c22af1ff314",
    },
}


def page_uuid(iri: str) -> uuid.UUID:
    """The UUID of an ``Item:OSW<hex>`` page."""
    return uuid.UUID(hex=iri.split(":OSW", 1)[1])


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


def store(osw_obj, entity, namespace=None) -> None:
    """Store ``entity``, replacing the page of an earlier run."""
    param = {"namespace": namespace} if namespace else {}
    osw_obj.store_entity(
        OswExpress.StoreEntityParam(entities=[entity], overwrite=True, **param)
    )


def build_dataset(osw_obj, spec: dict, fmt: str = "json"):
    """Import the cycler file and move its rows into a WikiFile.

    ``fmt`` picks the serialization: ``json`` keeps the typed values, ``csv``
    writes the pint-pandas frame with its unit header row.
    """
    source = MACCOR_DIR / spec["source"]
    dataset = read_maccor(source, fmt="mims_client1")
    print(f"Imported {len(dataset.data)} rows from {source.name}")

    dataset.uuid = page_uuid(spec["dataset"])
    dataset.label = [Label(text=f"{spec['name']} Dataset")]
    wiki_file = RerunWikiFileController(
        uuid=uuid.uuid5(NAMESPACE, f"file:{spec['dataset']}:{fmt}"),
        label=dataset.label,
        name=f"cycling_data.{fmt}",
        osw=osw_obj,
    )
    distribution = dataset.externalize_data(wiki_file, fmt=fmt)
    # externalize_data passes no store options to the upload, which keeps an
    # existing file page unchanged, so write the file metadata again
    store(osw_obj, wiki_file.cast(osw_model.WikiFile), namespace=wiki_file.namespace)
    print(
        f"Uploaded WikiFile: {distribution.download_url} "
        f"({distribution.byte_size} bytes)"
    )
    return dataset


def upload(
    osw_obj, spec: dict, fmt: str = "json"
) -> ProcedureLinkedElectrochemicalTest:
    dataset = build_dataset(osw_obj, spec, fmt)

    test = ProcedureLinkedElectrochemicalTest(
        uuid=page_uuid(spec["test"]),
        label=[Label(text=spec["name"])],
        device_under_test=[spec["cell"]],
        test_procedure=[
            LinkedTestProcedureItem(
                test_procedure_subcategory=AGING_PROCEDURE,
                test_procedure_instance=spec["procedure"],
                test_procedure_instance_property="Property:HasProcedure",
            )
        ],
        output=[dataset],
    )

    # The dataset goes first: its own page keeps the distribution.
    store(osw_obj, dataset)
    print(f"Stored dataset {dataset.get_iri()}")
    store(osw_obj, test)
    print(f"Stored test {test.get_iri()}")

    # Read the rows back out of the distribution, the way the dashboard does.
    rows = dataset.materialize_data()
    print(f"Round trip: {len(rows)} rows materialized back from the {fmt} file")
    return test


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dataset", choices=sorted(DATASETS), default="cell_a")
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
    # mwclient uploads files above chunk_size in chunks and then returns the
    # response wrapped in {"upload": ...}, which osw's upload check rejects
    # although the upload succeeded. One request avoids the chunked path.
    osw_obj.mw_site.chunk_size = UPLOAD_CHUNK_SIZE
    upload(osw_obj, spec, args.format)


if __name__ == "__main__":
    main()
