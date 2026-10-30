"""Log console, status line and step_type placeholders of the lazy view.

Driven by the in-memory backend of ``_lazy_backend``; no network.
"""

from __future__ import annotations

import logging

import pytest

pytest.importorskip("panel")
pytest.importorskip("panelini")

from _lazy_backend import MemoryBatteryBackend  # noqa: E402

from opensemantic.batteries.view import LazyBatteryDataView  # noqa: E402

LOGGER = "opensemantic.batteries.view"


def _select(view: LazyBatteryDataView) -> None:
    view._sel_instances["cell"] = {"Item:CellA", "Item:CellB"}
    view._sel_instances["proc"] = {"Item:Formation"}
    view._apply_selection()


@pytest.fixture
def logger():
    log = logging.getLogger(LOGGER)
    level, handlers = log.level, list(log.handlers)
    yield log
    log.setLevel(level)
    for handler in list(log.handlers):
        if handler not in handlers:
            log.removeHandler(handler)


def test_records_appear_newest_first_and_clear_empties(logger):
    view = LazyBatteryDataView(backend=MemoryBatteryBackend(), show_log=True)
    assert view._log_card.visible
    logger.info("first record")
    logger.info("second <b>record</b>")
    text = view._log_pane.object
    assert text.index("second") < text.index("first")
    assert "&lt;b&gt;" in text  # HTML-escaped
    assert " INFO " in text
    view._clear_log()
    assert view._log_pane.object == ""


def test_log_is_capped(logger):
    view = LazyBatteryDataView(backend=MemoryBatteryBackend(), show_log=True)
    for i in range(600):
        logger.info("record %d", i)
    assert view._log_pane.object.count("<div>") == 500
    assert "record 599" in view._log_pane.object
    assert "record 99<" not in view._log_pane.object


def test_second_view_replaces_the_handler(logger):
    before = len(logger.handlers)
    first = LazyBatteryDataView(backend=MemoryBatteryBackend(), show_log=True)
    second = LazyBatteryDataView(backend=MemoryBatteryBackend(), show_log=True)
    logger.info("only second")
    assert "only second" in second._log_pane.object
    assert "only second" not in first._log_pane.object
    assert len(logger.handlers) == before + 1


def test_selection_status_history(logger):
    view = LazyBatteryDataView(backend=MemoryBatteryBackend(), show_log=True)
    _select(view)
    assert len(view._status_history) == 2
    assert view._status_history[0] == "Querying datasets for 2 cells x 1 procedures..."
    assert view._status_history[1].startswith("Done: 2 datasets in ")
    assert view._status_pane.object == view._status_history[1]
    assert "selection: cells" in view._log_pane.object


def test_failed_query_sets_failed_status(logger):
    class Failing(MemoryBatteryBackend):
        def datasets(self, cell_iris, proc_iris):
            raise RuntimeError("wiki down")

    view = LazyBatteryDataView(backend=Failing(), show_log=True)
    _select(view)
    assert view._status_history[-1] == "Failed: wiki down"


def test_failed_wiki_queries_are_named_in_the_status(logger):
    class PartlyFailing(MemoryBatteryBackend):
        failed_queries = 0

        def datasets(self, cell_iris, proc_iris):
            self.failed_queries += 2
            return []

    view = LazyBatteryDataView(backend=PartlyFailing(), show_log=True)
    _select(view)
    assert view._status_history[-1].startswith("Done: 0 datasets in ")
    assert view._status_history[-1].endswith("(2 wiki queries failed, see log)")


def test_show_log_false_leaves_the_log_off(logger):
    handlers = list(logger.handlers)
    view = LazyBatteryDataView(backend=MemoryBatteryBackend())
    assert not view._log_card.visible
    assert view._log_handler is None
    assert list(logger.handlers) == handlers
    assert view._status_pane not in view.main_cards
    _select(view)
    assert view._status_history == []


def test_cell_without_parent_warns_once(logger, caplog):
    caplog.set_level(logging.WARNING, logger=LOGGER)
    view = LazyBatteryDataView(backend=MemoryBatteryBackend())
    _select(view)  # the trees were not walked, so no parent is known
    view._suggest_batches("cell")
    view._suggest_batches("cell")
    warned = [r.getMessage() for r in caplog.records if "no parent" in r.getMessage()]
    assert len(warned) == 2  # Cell A and Cell B, once each
    assert set(view._batch_assign.values()) == {view._NO_BATCH}


def test_placeholders_name_datasets_without_step_type(logger):
    view = LazyBatteryDataView(backend=MemoryBatteryBackend())
    _select(view)
    view._suggest_batches("cell")
    expected = "2 of 2 datasets have no step_type, so charge and discharge"
    assert expected in view._cycle_col.objects[0].object
    assert expected in view._batch_col.objects[0].object


def test_explicit_logger_level_is_kept(logger):
    logger.setLevel(logging.WARNING)
    LazyBatteryDataView(backend=MemoryBatteryBackend(), show_log=True)
    assert logger.level == logging.WARNING


def test_unset_logger_level_becomes_info(logger):
    logger.setLevel(logging.NOTSET)
    LazyBatteryDataView(backend=MemoryBatteryBackend(), show_log=True)
    assert logger.level == logging.INFO


def test_empty_selection_replaces_a_stale_status(logger):
    view = LazyBatteryDataView(backend=MemoryBatteryBackend(), show_log=True)
    _select(view)
    assert view._status_pane.object.startswith("Done:")
    view._sel_instances["cell"] = set()
    view._apply_selection()
    assert view._status_pane.object.startswith("No query: select at least one cell")


def test_scheduled_tick_with_empty_selection_replaces_querying(logger):
    view = LazyBatteryDataView(backend=MemoryBatteryBackend(), show_log=True)
    view._set_status("Querying datasets for 2 cells x 1 procedures...")
    view._selected_cell_ids = []
    view._selected_proc_ids = ["Item:Formation"]
    view._finish_selection()
    assert view._status_pane.object.startswith("No query:")


def test_placeholder_separates_empty_and_step_type_less_datasets(logger):
    view = LazyBatteryDataView(backend=MemoryBatteryBackend())
    rows = view._backend.datasets(["Item:CellA"], ["Item:Formation"])[0].rows
    view._matches = [
        {"idx": 0, "id": "a", "label": "a", "rows": []},
        {"idx": 1, "id": "b", "label": "b", "rows": rows},
    ]
    note = view._step_type_note("cycle")
    assert "1 of 2 datasets have no rows (nothing loaded)" in note
    assert "1 of 2 datasets have no step_type" in note
    view._matches = [{"idx": 0, "id": "a", "label": "a", "rows": []}]
    assert view._step_type_note("cycle") == (
        "_No cycle data: 1 of 1 dataset has no rows (nothing loaded)._"
    )
