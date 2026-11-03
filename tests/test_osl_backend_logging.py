"""Logging and test mapping of :class:`OSLBatteryBackend` over a fake wiki.

No network: a fake ``osw`` object answers ``semantic_search``, ``ask``,
``get_page_content`` and ``load_entity`` from tables.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

pytest.importorskip("panel")

from opensemantic.batteries.view._osl_backend import OSLBatteryBackend  # noqa: E402

LOGGER = "opensemantic.batteries.view"


def _page(title: str) -> Dict[str, Any]:
    return {"fulltext": title}


def _test_entry(duts: List[str], procs: List[str], outs: List[str]) -> Dict:
    return {
        "printouts": {
            "HasDut": [_page(t) for t in duts],
            "HasProcedure": [_page(t) for t in procs],
            "HasOutput": [_page(t) for t in outs],
        }
    }


class _FakeSite:
    def __init__(self, tests: Dict[str, Dict], search_script=None) -> None:
        self.tests = tests
        self.search_calls = 0
        self.ask_queries: List[str] = []
        self.search_script = search_script or (lambda n, q: ["Item:D1"])
        self._site = SimpleNamespace(api=self._api)

    def SearchParam(self, query: str, debug: bool = False):  # noqa: N802
        return {"query": query}

    def semantic_search(self, param):
        self.search_calls += 1
        return self.search_script(self.search_calls, param["query"])

    def get_page_content(self, titles):
        title = titles[0]
        return SimpleNamespace(contents={title: {"jsondata": {"name": f"n-{title}"}}})

    def _api(self, action: str, query: str):
        assert action == "ask"
        self.ask_queries.append(query)
        head = query.split("]]")[0]
        wanted = set(head[len("[[HasOutput::") :].split("||"))
        results = {
            name: entry
            for name, entry in self.tests.items()
            if wanted & {o["fulltext"] for o in entry["printouts"]["HasOutput"]}
        }
        return {"query": {"results": results if results else []}}


class _FakeOsw:
    def __init__(self, tests=None, search_script=None) -> None:
        self.site = _FakeSite(tests or {}, search_script)

    def load_entity(self, title: str):
        return SimpleNamespace(data=[1, 2, 3])


def _backend(osw: _FakeOsw, cache_dir=None) -> OSLBatteryBackend:
    return OSLBatteryBackend(
        osw_obj=osw,
        row_class=object,
        disjunctive_query=True,
        cache_dir=cache_dir,
    )


def _messages(caplog, level: int) -> List[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == level]


def test_search_logs_start_and_end(caplog):
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    backend = _backend(_FakeOsw())
    assert backend._search("[[HasSchema::Category:X]]") == ["Item:D1"]
    debug = _messages(caplog, logging.DEBUG)
    info = _messages(caplog, logging.INFO)
    assert any("[[HasSchema::Category:X]]" in m for m in debug)
    assert any("1 hits" in m and " s," in m and "0 retries" in m for m in info)


def test_attribute_error_is_retried_and_logged(caplog):
    caplog.set_level(logging.DEBUG, logger=LOGGER)

    def script(n, query):
        if n < 3:
            raise AttributeError("'list' object has no attribute 'values'")
        return ["Item:D1"]

    backend = _backend(_FakeOsw(search_script=script))
    assert backend._search("q") == ["Item:D1"]
    # the first is reported with its origin, the rest stay at debug
    assert any(
        "hit an AttributeError, retrying" in m
        for m in _messages(caplog, logging.WARNING)
    )
    assert len([m for m in _messages(caplog, logging.DEBUG) if "retry" in m]) == 1
    assert any("2 retries" in m for m in _messages(caplog, logging.INFO))


def test_retries_exhausted_warns_and_returns_empty(caplog):
    caplog.set_level(logging.DEBUG, logger=LOGGER)

    def script(n, query):
        raise AttributeError("flaky")

    osw = _FakeOsw(search_script=script)
    backend = _backend(osw)
    assert backend._search("q") == []
    assert osw.site.search_calls == OSLBatteryBackend._SEARCH_RETRIES
    assert backend.failed_queries == 1
    assert any("gave up" in m for m in _messages(caplog, logging.WARNING))


def test_other_exception_warns_and_returns_empty(caplog):
    caplog.set_level(logging.DEBUG, logger=LOGGER)

    def script(n, query):
        raise RuntimeError("boom")

    backend = _backend(_FakeOsw(search_script=script))
    assert backend._search("q") == []
    assert backend.failed_queries == 1
    assert any("boom" in m for m in _messages(caplog, logging.WARNING))


def test_page_name_cache_hit_is_logged(caplog):
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    backend = _backend(_FakeOsw())
    assert backend.page_name("Item:D1") == "n-Item:D1"
    assert backend.page_name("Item:D1") == "n-Item:D1"
    debug = _messages(caplog, logging.DEBUG)
    assert any("cache miss" in m for m in debug)
    assert any("cache hit" in m for m in debug)


def test_frame_load_is_logged(caplog):
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    backend = _backend(_FakeOsw())
    backend._df_for("Item:D1")
    backend._df_for("Item:D1")
    assert any(
        "loaded Item:D1: 3 rows, source inline" in m
        for m in _messages(caplog, logging.INFO)
    )
    assert any(
        "frame Item:D1: cache hit" in m for m in _messages(caplog, logging.DEBUG)
    )


def test_datasets_fill_cell_and_procedure(caplog):
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    tests = {
        "Item:T1": _test_entry(["Item:C1"], ["Item:P1"], ["Item:D1"]),
        # several cells and procedures: the selected ones win
        "Item:T2": _test_entry(
            ["Item:Cx", "Item:C2"], ["Item:Px", "Item:P2"], ["Item:D2"]
        ),
    }
    osw = _FakeOsw(tests, search_script=lambda n, q: ["Item:D1", "Item:D2", "Item:D3"])
    result = _backend(osw).datasets(["Item:C1", "Item:C2"], ["Item:P1", "Item:P2"])
    by_id = {d.id: d for d in result}
    assert (by_id["Item:D1"].cell_iri, by_id["Item:D1"].procedure_iri) == (
        "Item:C1",
        "Item:P1",
    )
    assert (by_id["Item:D2"].cell_iri, by_id["Item:D2"].procedure_iri) == (
        "Item:C2",
        "Item:P2",
    )
    # no test: left empty with a warning
    assert by_id["Item:D3"].cell_iri is None
    assert by_id["Item:D3"].procedure_iri is None
    assert any(
        "Item:D3" in m and "no linked test" in m
        for m in _messages(caplog, logging.WARNING)
    )
    assert len(osw.site.ask_queries) == 1


def test_dataset_with_two_tests_takes_the_first_and_warns(caplog):
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    tests = {
        "Item:T1": _test_entry(["Item:C1"], ["Item:P1"], ["Item:D1"]),
        "Item:T2": _test_entry(["Item:C2"], ["Item:P2"], ["Item:D1"]),
    }
    osw = _FakeOsw(tests, search_script=lambda n, q: ["Item:D1"])
    (dataset,) = _backend(osw).datasets(["Item:C1", "Item:C2"], ["Item:P1", "Item:P2"])
    assert (dataset.cell_iri, dataset.procedure_iri) == ("Item:C1", "Item:P1")
    assert any("2 tests" in m for m in _messages(caplog, logging.WARNING))


def test_many_titles_use_several_ask_calls():
    titles = [f"Item:D{i}" for i in range(120)]
    osw = _FakeOsw({}, search_script=lambda n, q: titles)
    backend = _backend(osw)
    backend._df_cache.update({t: None for t in titles})
    backend._name_cache.update({t: t for t in titles})
    assert len(backend.datasets(["Item:C1"], ["Item:P1"])) == 120
    assert len(osw.site.ask_queries) == 3  # 50 + 50 + 20
    assert all(q.count("Item:D") <= 50 for q in osw.site.ask_queries)


def test_a_second_load_comes_from_the_disk_cache(caplog, tmp_path):
    """A restart must not re-download what it already has."""
    caplog.set_level(logging.INFO, logger=LOGGER)
    backend = _backend(_FakeOsw(), cache_dir=tmp_path / "frames")
    backend._df_for("Item:D1")

    fresh = _backend(_FakeOsw(), cache_dir=tmp_path / "frames")
    fresh._df_for("Item:D1")

    messages = _messages(caplog, logging.INFO)
    assert any("source inline, downloaded" in m for m in messages)
    assert any("source inline, cached" in m for m in messages)


def test_an_unexplained_attribute_error_is_reported_with_its_origin(caplog):
    """The retry hides where it comes from; the first one has to say."""
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    backend = _backend(_FakeOsw())
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            result = {"query": {"results": {}}}
            return result.missing  # the shape the wiki sometimes answers with
        return ["Item:D1"]

    assert backend._call_with_retries("query", "[[X]]", flaky) == ["Item:D1"]

    warnings = _messages(caplog, logging.WARNING)
    assert any("hit an AttributeError, retrying" in m for m in warnings)
    assert any("raised at" in m and "result=" in m for m in warnings)


def test_a_retry_that_never_clears_is_counted_as_a_failure(caplog):
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    backend = _backend(_FakeOsw())

    def always():
        result = {}
        return result.missing

    assert backend._call_with_retries("query", "[[X]]", always) is None
    assert backend.failed_queries == 1
