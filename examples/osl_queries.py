"""The semantic searches the OSL dashboard backend runs, as a runnable script.

``OSLBatteryBackend`` answers the view's questions with the queries below; this
script runs any of them against a wiki so their result can be inspected on its
own. Category and instance IRIs come from the generated classes where possible,
so they do not have to be kept in sync by hand.

Run from the ``examples`` directory, after filling in ``accounts.pwd.yaml``::

    python osl_queries.py                     # list the available queries
    python osl_queries.py --query datasets

Needs the optional ``osl`` extra::

    pip install -e ".[osl]"
"""

from __future__ import annotations

import argparse
from pathlib import Path

from osw.defaults import params as default_params
from osw.defaults import paths as default_paths
from osw.express import OswExpress

from opensemantic.batteries.v1 import (
    BatteryCell,
    ElectrochemicalTest,
    ElectrochemicalTestProcedure,
)

WIKI_DOMAIN = "wiki-dev.open-semantic-lab.org"
CRED_FILEPATH = Path("accounts.pwd.yaml")

# Instances referenced by the dataset query, created by upload_example_data_OSL.py.
CELL_B = "Item:OSW4a20efb16be64868ab9d16a97838434a"
AGING_TEST_A = "Item:OSW365966aaa8d64804b5ff0351c9db5382"


def _cls_iri(cls) -> str:
    iri = cls.get_cls_iri()
    return iri[0] if isinstance(iri, list) else iri


def queries() -> dict:
    """Query name to SMW query string."""
    cell = _cls_iri(BatteryCell)
    procedure = _cls_iri(ElectrochemicalTestProcedure)
    test = _cls_iri(ElectrochemicalTest)
    return {
        # the two tree-building questions
        "cell_subcategories": f"[[SubClassOf::{cell}]]",
        "cell_instances": f"[[HasSchema::{cell}]]",
        "procedure_subcategories": f"[[SubClassOf::{procedure}]]",
        "procedure_instances": f"[[HasSchema::{procedure}]]",
        # tests of one cell, then narrowed to one procedure
        "tests_of_cell": f"[[HasSchema::{test}]][[HasDut::{CELL_B}]]",
        "tests_of_cell_and_procedure": (
            f"[[HasSchema::{test}]][[HasDut::{CELL_B}]]"
            f"[[HasProcedure::{AGING_TEST_A}]]"
        ),
        # the datasets themselves: pages that are the HasOutput of such a test
        "datasets": (
            f"[[-HasOutput.HasSchema::{test}]][[-HasOutput.HasDut::{CELL_B}]]"
            f"[[-HasOutput.HasProcedure::{AGING_TEST_A}]]"
        ),
    }


def main() -> None:
    available = queries()
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--query", choices=sorted(available))
    parser.add_argument("--domain", default=WIKI_DOMAIN)
    parser.add_argument("--cred-filepath", type=Path, default=CRED_FILEPATH)
    args = parser.parse_args()

    if not args.query:
        for name, query in sorted(available.items()):
            print(f"{name}\n    {query}\n")
        return

    default_paths.cred_filepath = args.cred_filepath
    default_params.wiki_domain = args.domain
    osw_obj = OswExpress(domain=args.domain, cred_filepath=args.cred_filepath)

    query = available[args.query]
    print(f"{args.query}: {query}")
    results = osw_obj.site.semantic_search(
        osw_obj.site.SearchParam(query=query, debug=False)
    )
    for result in results:
        print(f"  {result}")


if __name__ == "__main__":
    main()
