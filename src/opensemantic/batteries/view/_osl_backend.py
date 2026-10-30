"""OSL (Open Semantic Lab) backend for the lazy battery dashboard.

Reference :class:`~opensemantic.batteries.view._backend.BatteryDataBackend`
implementation over a **live OSL wiki**. It answers the view's four questions
with semantic searches (see ``examples/osl_queries.py`` for the raw calls):

* sub-categories of a category ``C``   ``[[SubClassOf::C]]``
* instances of a category ``C``        ``[[HasSchema::C]]``
* datasets for a cell + procedure
                                       ``[[-HasOutput.HasSchema::<ElectrochemicalTest>]]``
                                       ``[[-HasOutput.HasDut::<cell>]]``
                                       ``[[-HasOutput.HasProcedure::<proc>]]``

Category IRIs default to the generated classes' ``get_cls_iri()`` — no
hand-copied OSW ids. The dataset query uses the *inverse-output* path (a page
that is the ``HasOutput`` of a matching test), so each result is a cycling
dataset whose rows come straight from ``osw_obj.load_entity(<title>).data``.

A cycling dataset can carry its rows in one of two ways, both handled by
:meth:`OSLBatteryBackend._entity_rows`:

* **inline** — rows live in the dataset's ``data`` attribute (small datasets), or
* **out-of-band** — for large datasets the rows are split into a WikiFile and
  ``data`` is cleared; the file is referenced from the dataset's first
  ``Distribution`` (``distributions[0].download_url``, a full wiki file URL). The
  backend downloads that file, parses its bare ``data`` array and rebuilds the
  rows in memory (the reverse of ``examples/upload_large_dataset_OSL.py``).

``osw`` is imported lazily (only :func:`connect_osw` and the out-of-band download
path need it), so importing this module — and the whole ``view`` package — never
requires the optional ``osw`` dependency. Install it with the ``osl`` extra to
actually connect.
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from opensemantic.batteries._cycling import CyclingDatasetController
from opensemantic.batteries.v1 import (
    BatteryCell,
    ElectrochemicalTest,
    ElectrochemicalTestProcedure,
)
from opensemantic.batteries.view._backend import (
    BatteryDataBackend,
    Dataset,
    TreeNode,
)

DEFAULT_WIKI_DOMAIN = "wiki-dev.open-semantic-lab.org"

_log = logging.getLogger("opensemantic.batteries.view")

# Titles per ``ask`` query when the tests of the datasets are looked up
_ASK_CHUNK = 50


def connect_osw(
    cred_filepath: Union[str, Path],
    wiki_domain: str = DEFAULT_WIKI_DOMAIN,
) -> Optional[Any]:
    """Open an ``OswExpress`` session, or return ``None`` if it can't be made.

    Returning ``None`` (rather than raising) lets a dashboard load without a
    wiki connection — handy for a quick UI smoke test; every query then no-ops.
    The credentials file (a ``.pwd.yaml`` secret) is referenced only by path and
    never read here — keep it out of version control.
    """
    try:
        from osw.defaults import params as default_params
        from osw.defaults import paths as default_paths
        from osw.express import OswExpress

        cred = Path(cred_filepath)
        default_paths.cred_filepath = cred
        default_params.wiki_domain = wiki_domain
        return OswExpress(domain=wiki_domain, cred_filepath=cred)
    except Exception as exc:  # noqa: BLE001 — degrade gracefully, log why
        _log.warning("no OSL connection: %s", exc)
        return None


class OSLBatteryBackend(BatteryDataBackend):
    """Battery data served from a live OSL wiki via ``OswExpress``.

    Holds the wiki session, the tree-root / test category IRIs and the row
    model, plus small caches (page names, loaded rows) so panning around the
    trees doesn't re-hit the wiki. When ``osw_obj`` is ``None`` every method
    degrades to empty results, so the dashboard still builds offline.
    """

    # ``semantic_search`` intermittently raises ``AttributeError: 'list' object
    # has no attribute 'values'`` — even for queries that *do* have results (the
    # same query flip-flops between the list and the error across calls). So the
    # AttributeError is retried a few times before being taken as "truly empty",
    # otherwise valid rows would randomly vanish from the dashboard.
    _SEARCH_RETRIES = 4

    def __init__(
        self,
        osw_obj: Optional[Any],
        row_class: type,
        cell_root_iri: Optional[str] = None,
        procedure_root_iri: Optional[str] = None,
        test_category_iri: Optional[str] = None,
        field_names: Optional[List[str]] = None,
        cell_root_label: str = BatteryCell.__name__,
        procedure_root_label: str = ElectrochemicalTestProcedure.__name__,
        disjunctive_query: bool = False,
    ) -> None:
        self._osw = osw_obj
        self._row_class = row_class
        # ``True``  -> one disjunctive (``||``) search covering every selected
        #              cell × procedure at once (default: fewest round-trips).
        # ``False`` -> one search per (cell, procedure) pair (the older, more
        #              conservative path — use if a wiki mishandles ``||``).
        self._disjunctive = disjunctive_query
        # Wiki calls that failed (error or retries exhausted). A failed call
        # returns an empty result, so the view reads this counter to tell a
        # failure from a query without hits.
        self.failed_queries = 0
        self._cell_root_iri = cell_root_iri or BatteryCell.get_cls_iri()
        self._proc_root_iri = (
            procedure_root_iri or ElectrochemicalTestProcedure.get_cls_iri()
        )
        self._test_category_iri = test_category_iri or ElectrochemicalTest.get_cls_iri()
        self._field_names = field_names
        self._cell_root_label = cell_root_label
        self._proc_root_label = procedure_root_label

        # Caches so panning around the trees doesn't re-hit the wiki.
        self._name_cache: dict = {}
        self._rows_cache: dict = {}

    # -- Row model -----------------------------------------------------------

    @property
    def row_class(self) -> type:
        return self._row_class

    @property
    def field_names(self) -> List[str]:
        if self._field_names is not None:
            return self._field_names
        return super().field_names

    # -- Trees ---------------------------------------------------------------

    @property
    def cell_root(self) -> TreeNode:
        return TreeNode(self._cell_root_iri, self._cell_root_label, "class")

    @property
    def procedure_root(self) -> TreeNode:
        return TreeNode(self._proc_root_iri, self._proc_root_label, "class")

    def children(self, category_iri: str) -> List[TreeNode]:
        """Sub-categories (class nodes) + instances (leaf nodes) of a category."""
        nodes = [
            TreeNode(sub, self.page_name(sub), "class")
            for sub in self._subclasses(category_iri)
        ]
        nodes += [
            TreeNode(inst, self.page_name(inst), "instance")
            for inst in self._instances(category_iri)
        ]
        return nodes

    # -- Query ---------------------------------------------------------------

    def datasets(self, cell_iris: List[str], proc_iris: List[str]) -> List[Dataset]:
        """Cycling datasets for the selected cells × procedures, de-duplicated.

        By default (``disjunctive_query=True``) this is a **single** semantic
        search that ``||``-ORs every selected cell and every selected procedure,
        so N cells × M procedures cost one round-trip instead of N×M. Set
        ``disjunctive_query=False`` to fall back to one search per pair. Either
        way the matching page titles are de-duplicated and their rows resolved
        (cached); matching happens server-side, so every returned page is a
        genuine match.
        """
        if not cell_iris or not proc_iris or self._osw is None:
            return []
        if self._disjunctive:
            titles = self._dataset_titles_or(cell_iris, proc_iris)
        else:
            titles = self._dataset_titles_per_pair(cell_iris, proc_iris)
        unique = list(dict.fromkeys(titles))
        mapping = self._test_mapping(unique, cell_iris, proc_iris)
        result: List[Dataset] = []
        for title in unique:
            cell_iri, proc_iri = mapping.get(title, (None, None))
            result.append(
                Dataset(
                    id=title,
                    label=self.page_name(title),
                    rows=self._rows_for(title),
                    cell_iri=cell_iri,
                    procedure_iri=proc_iri,
                )
            )
        return result

    def _test_mapping(
        self, titles: List[str], cell_iris: List[str], proc_iris: List[str]
    ) -> Dict[str, Tuple[Optional[str], Optional[str]]]:
        """Map each dataset title to ``(cell iri, procedure iri)`` of its test.

        One read-only ``ask`` query per chunk of at most ``_ASK_CHUNK`` titles
        fetches the tests whose ``HasOutput`` is one of the titles, with the
        printouts ``HasDut``, ``HasProcedure`` and ``HasOutput``. When a test
        names several cells or procedures, the first one that is selected wins.
        A dataset with several tests takes the first test and logs a warning; a
        dataset without a test stays ``(None, None)`` and logs a warning.
        """
        mapping: Dict[str, Tuple[Optional[str], Optional[str]]] = {}
        tests_of: Dict[str, List[str]] = {title: [] for title in titles}
        for start in range(0, len(titles), _ASK_CHUNK):
            chunk = titles[start : start + _ASK_CHUNK]
            for test_title, printouts in self._ask_tests(chunk).items():
                duts = self._printout_titles(printouts.get("HasDut"))
                procs = self._printout_titles(printouts.get("HasProcedure"))
                cell = next((d for d in duts if d in cell_iris), None)
                proc = next((p for p in procs if p in proc_iris), None)
                pair = (
                    cell or (duts[0] if duts else None),
                    proc or (procs[0] if procs else None),
                )
                for out in self._printout_titles(printouts.get("HasOutput")):
                    if out not in tests_of:
                        continue
                    tests_of[out].append(test_title)
                    mapping.setdefault(out, pair)
        for title, tests in tests_of.items():
            if not tests:
                _log.warning("dataset %s has no linked test", title)
            elif len(tests) > 1:
                _log.warning(
                    "dataset %s has %d tests (%s), using the first",
                    title,
                    len(tests),
                    ", ".join(tests),
                )
        return mapping

    @staticmethod
    def _printout_titles(values: Optional[List[Any]]) -> List[str]:
        """Page titles of an ``ask`` printout (a list of page dicts)."""
        titles = []
        for value in values or []:
            title = value.get("fulltext") if isinstance(value, dict) else value
            if title:
                titles.append(str(title))
        return titles

    def _ask_tests(self, titles: List[str]) -> Dict[str, Dict[str, Any]]:
        """``{test title: printouts}`` of the tests that output one of titles."""
        query = (
            f"[[HasOutput::{'||'.join(titles)}]]"
            "|?HasDut|?HasProcedure|?HasOutput|limit=500"
        )
        response = self._call_with_retries(
            "ask",
            query,
            lambda: self._osw.site._site.api("ask", query=query),
            lambda r: len((r.get("query") or {}).get("results") or {}),
        )
        if not response:
            return {}
        if response.get("query-continue-offset"):
            _log.warning("ask result is truncated at 500 tests")
        results = (response.get("query") or {}).get("results") or {}
        if isinstance(results, list):  # SMW answers a list when it is empty
            return {}
        return {
            title: (entry.get("printouts") or {}) for title, entry in results.items()
        }

    def _dataset_titles_or(
        self, cell_iris: List[str], proc_iris: List[str]
    ) -> List[str]:
        """One disjunctive search over all selected cells × procedures.

        ``[[-HasOutput.HasDut::a||b]]`` matches a test whose DUT is *any* of the
        listed cells; likewise for the procedure. Combined with the schema
        clause this yields every dataset for any (selected cell, selected
        procedure) pairing in a single query.
        """
        return self._datasets_for("||".join(cell_iris), "||".join(proc_iris))

    def _dataset_titles_per_pair(
        self, cell_iris: List[str], proc_iris: List[str]
    ) -> List[str]:
        """One search per (cell, procedure) pair (opt-in conservative path)."""
        titles: List[str] = []
        for cell in cell_iris:
            for proc in proc_iris:
                titles.extend(self._datasets_for(cell, proc))
        return titles

    # -- Labels --------------------------------------------------------------

    def page_name(self, iri: str) -> str:
        """Human-readable ``name`` for a page title, cached (falls back to title)."""
        if iri in self._name_cache:
            _log.debug("page_name %s: cache hit", iri)
            return self._name_cache[iri]
        _log.debug("page_name %s: cache miss, fetching", iri)
        name = iri
        if self._osw is not None:
            try:
                content = self._osw.site.get_page_content([iri]).contents[iri]
                name = content["jsondata"].get("name", iri)
            except Exception as exc:  # noqa: BLE001
                _log.warning("page_name %s failed: %s", iri, exc)
        self._name_cache[iri] = name
        return name

    # -- Wiki queries (see examples/osl_queries.py) ------------------------------

    def _search(self, query: str) -> List[str]:
        """Run a semantic search, returning page titles (``[]`` on empty/failure)."""
        if self._osw is None:
            return []
        hits = self._call_with_retries(
            "query",
            query,
            lambda: list(
                self._osw.site.semantic_search(
                    self._osw.site.SearchParam(query=query, debug=False)
                )
            ),
        )
        return hits or []

    def _call_with_retries(
        self,
        kind: str,
        query: str,
        call: Callable[[], Any],
        count: Callable[[Any], int] = len,
    ) -> Any:
        """Run a wiki call with the ``AttributeError`` retries, logging each step.

        Returns the call's result, or ``None`` when the retries are exhausted or
        another exception occurs (both log a warning).
        """
        _log.debug("%s start: %s", kind, query)
        began = time.perf_counter()
        for attempt in range(self._SEARCH_RETRIES):
            try:
                result = call()
            except AttributeError as exc:
                # flaky zero-hit quirk - retry, then treat as empty
                _log.debug(
                    "%s retry %d/%d after AttributeError: %s",
                    kind,
                    attempt + 1,
                    self._SEARCH_RETRIES,
                    exc,
                )
                continue
            except Exception as exc:  # noqa: BLE001
                _log.warning("%s failed: %s: %s", kind, query, exc)
                self.failed_queries += 1
                return None
            try:
                hits = count(result)
            except (TypeError, AttributeError):
                hits = 0
            _log.info(
                "%s done: %d hits in %.2f s, %d retries",
                kind,
                hits,
                time.perf_counter() - began,
                attempt,
            )
            return result
        _log.warning(
            "%s gave up after %d AttributeError retries: %s",
            kind,
            self._SEARCH_RETRIES,
            query,
        )
        self.failed_queries += 1
        return None

    def _subclasses(self, cat_iri: str) -> List[str]:
        return self._search(f"[[SubClassOf::{cat_iri}]]")

    def _instances(self, cat_iri: str) -> List[str]:
        return self._search(f"[[HasSchema::{cat_iri}]]")

    def _datasets_for(self, cell_iri: str, proc_iri: str) -> List[str]:
        """Cycling datasets output by an ElectrochemicalTest on this cell + proc.

        ``cell_iri`` / ``proc_iri`` may each be a single IRI or a ``||``-joined
        disjunction of IRIs (see :meth:`_dataset_titles_or`).

        Uses the inverse-property path from ``examples/osl_queries.py``: find each
        page that is the ``HasOutput`` of a test whose schema / DUT / procedure
        match. The results are the dataset pages themselves, so
        ``load_entity(title).data`` yields the rows directly — no second hop
        through the test. (The forward ``[[HasDut::…]][[HasProcedure::…]]`` combo
        does not reliably intersect on the wiki once the procedure link moved
        into ``TestProcedureItem``; the inverse-output path does.)
        """
        return self._search(
            f"[[-HasOutput.HasSchema::{self._test_category_iri}]]"
            f"[[-HasOutput.HasDut::{cell_iri}]]"
            f"[[-HasOutput.HasProcedure::{proc_iri}]]"
        )

    # -- Row loading ---------------------------------------------------------

    def _rows_for(self, title: str) -> List[Any]:
        """Load a test (or dataset) page and return its cycling rows, cached.

        ``load_entity`` yields either a dataset (has ``.data`` directly — as in
        Query.py) or an ``ElectrochemicalTest`` whose ``.output`` references one
        or more datasets; both are resolved to a flat list of rows.
        """
        if title in self._rows_cache:
            _log.debug("rows %s: cache hit", title)
            return self._rows_cache[title]
        rows: List[Any] = []
        if self._osw is not None:
            began = time.perf_counter()
            try:
                entity = self._osw.load_entity(title)
                if getattr(entity, "data", None):
                    source = "inline"
                elif getattr(entity, "distributions", None):
                    source = "file distribution"
                else:
                    source = "output datasets"
                rows = self._entity_rows(entity)
                _log.info(
                    "loaded %s: %d rows, source %s, %.2f s",
                    title,
                    len(rows),
                    source,
                    time.perf_counter() - began,
                )
            except Exception as exc:  # noqa: BLE001
                _log.warning("load_entity(%r) failed: %s", title, exc)
        self._rows_cache[title] = rows
        return rows

    def _entity_rows(self, entity: Any) -> List[Any]:
        data = getattr(entity, "data", None)
        if data:
            return list(data)
        # Large-dataset path: the inline ``data`` was cleared and the rows split
        # into a WikiFile referenced from a Distribution. Download and rebuild.
        rows = self._rows_from_distributions(entity)
        if rows:
            return rows
        outputs = getattr(entity, "output", None) or []
        if not isinstance(outputs, (list, tuple)):
            outputs = [outputs]
        rows: List[Any] = []
        for out in outputs:
            if isinstance(out, str):  # an IRI — load the referenced dataset
                if self._osw is None:
                    continue
                try:
                    out = self._osw.load_entity(out)
                except Exception:  # noqa: BLE001
                    continue
            d = getattr(out, "data", None)
            if d:
                rows.extend(d)
        return rows

    # -- Out-of-band (large-dataset) row loading -----------------------------

    def _rows_from_distributions(self, entity: Any) -> List[Any]:
        """Rebuild rows from a dataset whose ``data`` was split into a file.

        The dataset references the file through a ``Distribution``;
        ``DatasetControllerMixin.materialize_data`` reads it through the file
        controller registered for that file type and rebuilds the typed rows.
        The session goes with it: a wiki file read without one comes back as
        the login page.
        """
        if not getattr(entity, "distributions", None):
            return []
        try:
            controller = entity
            if not hasattr(controller, "materialize_data"):
                controller = entity.cast(CyclingDatasetController)
            controller.distributions = [
                self._with_wiki_file(dist) for dist in controller.distributions or []
            ]
            return list(controller.materialize_data(osw=self._osw) or [])
        except Exception as exc:  # noqa: BLE001 - degrade to empty rows
            _log.warning("materialize failed: %s", exc)
            return []

    def _with_wiki_file(self, distribution: Any) -> Any:
        """The distribution with its file resolved to the wiki controller.

        A distribution that names only a URL would otherwise be read with the
        generic remote controller, which fetches it with plain urllib - on a
        login-required wiki that returns the login page, not the rows.
        """
        if self._osw is None:
            return distribution
        try:
            # a reference resolves on access, so a dangling one raises here
            # rather than answering None - the URL below is then the way in
            if getattr(distribution, "file", None) is not None:
                return distribution
        except Exception as exc:  # noqa: BLE001 - fall through to the URL
            _log.warning("distribution file unreadable: %s", exc)
        url = getattr(distribution, "download_url", None) or getattr(
            distribution, "access_url", None
        )
        if not url or "File:" not in url:
            return distribution
        from osw.controller.file.wiki import WikiFileController

        # the page title, without a query string or fragment the URL may carry
        title = "File:" + re.split(r"[?#&]", url.split("File:")[-1])[0]
        wiki_file = self._osw.load_entity(title).cast(WikiFileController, osw=self._osw)
        return distribution.copy(update={"file": wiki_file})
