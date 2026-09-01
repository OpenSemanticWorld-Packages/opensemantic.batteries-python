"""Query-builder tests for :class:`OSLBatteryBackend.datasets`.

No live wiki: a tiny fake ``osw`` records every ``semantic_search`` query, so we
can assert exactly how many searches run and what disjunction they carry.

* Default (``disjunctive_query=True``) -> ONE ``||``-OR search over all selected
  cells × procedures.
* ``disjunctive_query=False`` -> one search per (cell, procedure) pair.
"""

from __future__ import annotations

from typing import Dict, List

import pytest

pytest.importorskip("panel")

from opensemantic.batteries.view._osl_backend import OSLBatteryBackend  # noqa: E402

TEST_CAT = "Category:ElectrochemicalTest"


class _FakeSite:
    def __init__(self, results: Dict[str, List[str]]) -> None:
        self._results = results
        self.queries: List[str] = []

    def SearchParam(self, query: str, debug: bool = False):  # noqa: N802
        return {"query": query}

    def semantic_search(self, param):
        self.queries.append(param["query"])
        return list(self._results.get(param["query"], []))


class _FakeOsw:
    def __init__(self, results: Dict[str, List[str]]) -> None:
        self.site = _FakeSite(results)


def _backend(results, *, disjunctive: bool) -> OSLBatteryBackend:
    b = OSLBatteryBackend(
        osw_obj=_FakeOsw(results),
        row_class=object,
        test_category_iri=TEST_CAT,
        disjunctive_query=disjunctive,
    )
    # Skip page-name / row round-trips to the (fake) wiki.
    for title in {t for titles in results.values() for t in titles}:
        b._name_cache[title] = title
        b._rows_cache[title] = []
    return b


def _q(cells: str, procs: str) -> str:
    return (
        f"[[-HasOutput.HasSchema::{TEST_CAT}]]"
        f"[[-HasOutput.HasDut::{cells}]]"
        f"[[-HasOutput.HasProcedure::{procs}]]"
    )


def test_disjunctive_is_default_single_query():
    query = _q("Item:CellA||Item:CellB", "Item:Formation")
    b = _backend({query: ["Ds:AB"]}, disjunctive=True)

    result = b.datasets(["Item:CellA", "Item:CellB"], ["Item:Formation"])

    assert b._osw.site.queries == [query]  # exactly one search
    assert [d.id for d in result] == ["Ds:AB"]


def test_per_pair_option_runs_one_query_per_pair():
    q_a = _q("Item:CellA", "Item:Formation")
    q_b = _q("Item:CellB", "Item:Formation")
    b = _backend({q_a: ["Ds:A"], q_b: ["Ds:B"]}, disjunctive=False)

    result = b.datasets(["Item:CellA", "Item:CellB"], ["Item:Formation"])

    assert b._osw.site.queries == [q_a, q_b]  # two searches, one per pair
    assert sorted(d.id for d in result) == ["Ds:A", "Ds:B"]


def test_results_are_de_duplicated_across_the_cross_product():
    # Per-pair mode: both pairs return the same dataset page -> one Dataset.
    q_a = _q("Item:CellA", "Item:Formation")
    q_b = _q("Item:CellB", "Item:Formation")
    b = _backend({q_a: ["Ds:Shared"], q_b: ["Ds:Shared"]}, disjunctive=False)

    result = b.datasets(["Item:CellA", "Item:CellB"], ["Item:Formation"])

    assert [d.id for d in result] == ["Ds:Shared"]


def test_empty_selection_runs_no_query():
    b = _backend({}, disjunctive=True)
    assert b.datasets([], ["Item:Formation"]) == []
    assert b.datasets(["Item:CellA"], []) == []
    assert b._osw.site.queries == []
