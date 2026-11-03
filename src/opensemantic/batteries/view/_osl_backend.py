"""OSL (Open Semantic Lab) backend for the lazy battery dashboard.

Reference :class:`~opensemantic.batteries.view._backend.BatteryDataBackend`
implementation over a **live OSL wiki**. It answers the view's four questions
with semantic searches (see ``examples/osl/osl_queries.py`` for the raw calls):

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
:meth:`OSLBatteryBackend._entity_df`, which answers with a frame either way:

* **inline** — rows live in the dataset's ``data`` attribute (small datasets), or
* **out-of-band** — for large datasets the rows are split into a WikiFile and
  ``data`` is cleared; the file is referenced from the dataset's
  ``Distribution`` (the reverse of ``examples/osl/upload_large_dataset_OSL.py``).
  The file is read straight into a frame, so a dataset of a hundred thousand
  rows costs what pandas needs rather than a model per cell.

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
from opensemantic.batteries.view._frame_cache import FrameCache, fingerprint_of

_DIAGNOSTIC_LOCALS = ("result", "response", "data", "results", "json")
"""Locals worth reporting from the frame an unexplained error came from."""


def _diagnose(exc: BaseException, limit: int = 400) -> str:
    """Where an exception came from and what the wiki had answered.

    The retry loop around the wiki calls swallows an error whose origin is
    not understood. Naming the frame it was raised in, and the response that
    was being read there, is what turns the next occurrence into something
    that can be fixed rather than retried.
    """
    tb = exc.__traceback__
    deepest = None
    while tb is not None:
        deepest = tb
        tb = tb.tb_next
    if deepest is None:
        return "no traceback"
    frame = deepest.tb_frame
    where = f"{frame.f_code.co_filename}:{deepest.tb_lineno} in {frame.f_code.co_name}"
    seen = []
    for name in _DIAGNOSTIC_LOCALS:
        if name in frame.f_locals:
            text = repr(frame.f_locals[name])
            if len(text) > limit:
                text = text[:limit] + "..."
            seen.append(f"{name}={text}")
    answered = "; ".join(seen) if seen else "no response in scope"
    return f"raised at {where}; {answered}"


_UNSET = object()
"""Tells "no cache_dir given" from an explicit ``None``, which disables it."""

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
        cache_dir: Optional[Any] = _UNSET,
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
        self._df_cache: dict = {}
        # ... and one on disk, so a restart does not re-download every
        # dataset. ``cache_dir=None`` turns it off.
        if cache_dir is _UNSET:
            self._frames: Optional[FrameCache] = FrameCache()
        else:
            self._frames = FrameCache(cache_dir) if cache_dir is not None else None

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
                    df=self._df_for(title),
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
        """``{test title: printouts}`` of the tests that output one of titles.

        Each printout is named in the query (``?HasDut=HasDut``). Without a
        name, SMW keys a printout by the property's label in the wiki's
        language, for example ``HatAusgangsressource`` for ``HasOutput``.
        """
        query = (
            f"[[HasOutput::{'||'.join(titles)}]]"
            "|?HasDut=HasDut|?HasProcedure=HasProcedure|?HasOutput=HasOutput"
            "|limit=500"
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

    # -- Wiki queries (see examples/osl/osl_queries.py) ------------------------------

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
                # The known cause was osw below 2.1.5, where a query without
                # hits raised "'list' object has no attribute 'values'"
                # because SMW serialises an empty result set as an array. The
                # osl extra now requires 2.1.5, so this should no longer fire.
                # It is kept as a net, and reports the frame it came from and
                # what the wiki answered, so a different cause is diagnosable
                # instead of being retried away.
                if attempt == 0:
                    _log.warning(
                        "%s hit an AttributeError, retrying. %s",
                        kind,
                        _diagnose(exc),
                        exc_info=True,
                    )
                else:
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

        Uses the inverse-property path from ``examples/osl/osl_queries.py``: find each
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

    def _df_for(self, title: str):
        """Load a test (or dataset) page and return its measurements, cached.

        ``load_entity`` yields either a dataset (has ``.data`` directly, as in
        Query.py) or an ``ElectrochemicalTest`` whose ``.output`` references one
        or more datasets; both are resolved to one frame.
        """
        if title in self._df_cache:
            _log.debug("frame %s: cache hit", title)
            return self._df_cache[title]
        df = None
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
                # the page says what its content is before it is downloaded,
                # so an unchanged dataset is read from disk instead
                fingerprint = fingerprint_of(entity)
                cache_key = self._cache_key(title)
                df = (
                    self._frames.get(cache_key, fingerprint)
                    if self._frames is not None
                    else None
                )
                if df is None:
                    df = self._entity_df(entity)
                    if self._frames is not None:
                        self._frames.put(cache_key, fingerprint, df)
                    source += ", downloaded"
                else:
                    source += ", cached"
                _log.info(
                    "loaded %s: %d rows, source %s, %.2f s",
                    title,
                    0 if df is None else len(df),
                    source,
                    time.perf_counter() - began,
                )
            except Exception as exc:  # noqa: BLE001
                _log.warning("load_entity(%r) failed: %s", title, exc)
        self._df_cache[title] = df
        return df

    def _cache_key(self, title: str) -> str:
        """Page title qualified by the wiki it came from.

        The cache is shared between wikis, and the same page title means
        different data on each of them.
        """
        domain = getattr(self._osw, "domain", None)
        if domain is None:
            site = getattr(self._osw, "site", None)
            domain = getattr(getattr(site, "mw_site", None), "host", None)
        return f"{domain or 'unknown'}|{title}"

    def _entity_df(self, entity: Any):
        data = getattr(entity, "data", None)
        if data:
            return self._rows_to_df(entity, data)
        # Large-dataset path: the inline ``data`` was cleared and the rows split
        # into a WikiFile referenced from a Distribution. Read it as a frame.
        df = self._df_from_distributions(entity)
        if df is not None and len(df):
            return df
        outputs = getattr(entity, "output", None) or []
        if not isinstance(outputs, (list, tuple)):
            outputs = [outputs]
        frames = []
        for out in outputs:
            if isinstance(out, str):  # an IRI, load the referenced dataset
                if self._osw is None:
                    continue
                try:
                    out = self._osw.load_entity(out)
                except Exception:  # noqa: BLE001
                    continue
            part = self._entity_df(out)
            if part is not None and len(part):
                frames.append(part)
        if not frames:
            return None
        if len(frames) == 1:
            return frames[0]
        import pandas as pd

        return pd.concat(frames, ignore_index=True)

    @staticmethod
    def _as_controller(entity: Any):
        """The entity as something that can convert and materialize itself."""
        if hasattr(entity, "materialize_df"):
            return entity
        return entity.cast(CyclingDatasetController)

    def _rows_to_df(self, entity: Any, rows: Any):
        """Inline rows as a frame, with their units where they are readable.

        A dataset loaded from the wiki converts itself. Anything else - a
        stand-in, a model from another package - still has rows worth showing,
        so they become a plain frame over the fields the backend declares.
        """
        import pandas as pd

        try:
            return self._as_controller(entity).to_df()
        except Exception as exc:  # noqa: BLE001 - fall back to the plain form
            _log.debug("%s cannot convert itself: %s", type(entity).__name__, exc)
        return pd.DataFrame(
            [
                {name: getattr(row, name, None) for name in self.field_names}
                for row in rows
            ]
        )

    # -- Out-of-band (large-dataset) loading ---------------------------------

    def _df_from_distributions(self, entity: Any):
        """Read a dataset whose ``data`` was split into a file, as a frame.

        The dataset references the file through a ``Distribution``;
        ``materialize_df`` reads it through the file controller registered for
        that file type, without building a model per cell. The session goes
        with it: a wiki file read without one comes back as the login page.
        """
        if not getattr(entity, "distributions", None):
            return None
        try:
            controller = self._as_controller(entity)
            controller.distributions = [
                self._with_wiki_file(dist) for dist in controller.distributions or []
            ]
            return controller.materialize_df(osw=self._osw)
        except Exception as exc:  # noqa: BLE001 - degrade to no data
            _log.warning("materialize failed: %s", exc)
            return None

    def _with_wiki_file(self, distribution: Any) -> Any:
        """The distribution with its file resolved to the wiki controller.

        A distribution that names only a URL would otherwise be read with the
        generic remote controller, which fetches it with plain urllib - on a
        login-required wiki that returns the login page, not the rows.

        A distribution written by the current ``externalize_data`` also names
        its file. That reference loads as a plain ``WikiFile`` entity, for
        which no file controller is registered, so it is cast to the wiki
        controller as well.
        """
        if self._osw is None:
            return distribution
        from osw.controller.file.wiki import WikiFileController

        try:
            # a reference resolves on access, so a dangling one raises here
            # rather than answering None - the URL below is then the way in
            file = getattr(distribution, "file", None)
            if file is not None:
                if callable(getattr(file, "get", None)):
                    return distribution  # already a file controller
                wiki_file = file.cast(WikiFileController, osw=self._osw)
                return distribution.copy(update={"file": wiki_file})
        except Exception as exc:  # noqa: BLE001 - fall through to the URL
            _log.warning("distribution file unreadable: %s", exc)
        url = getattr(distribution, "download_url", None) or getattr(
            distribution, "access_url", None
        )
        if not url or "File:" not in url:
            return distribution

        # the page title, without a query string or fragment the URL may carry
        title = "File:" + re.split(r"[?#&]", url.split("File:")[-1])[0]
        wiki_file = self._osw.load_entity(title).cast(WikiFileController, osw=self._osw)
        return distribution.copy(update={"file": wiki_file})
