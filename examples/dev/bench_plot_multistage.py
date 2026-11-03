"""Time the plot stages of the multi-stage dashboard without a server.

Loads the cells given on the command line (default 1-1 and 1-2) through the
backend of ``battery_dashboard_multistage.py``, then times each stage of a
selection: the query, the value conversion, the time-series figure, the cycle
and batch figures, and the Bokeh serialization of the time-series figure.
The file load is timed separately, so the plot stages run on cached frames.

The browser draw time is not measured here.

Run with:
    python examples/dev/bench_plot_multistage.py [cell ...]
"""

from __future__ import annotations

import json
import runpy
import sys
import time
from pathlib import Path

from bokeh.embed import json_item
from bokeh.models import ColumnDataSource

EXAMPLE = Path(__file__).parent / "battery_dashboard_multistage.py"


def _time(label: str, fn, *args):
    began = time.perf_counter()
    result = fn(*args)
    print(f"{label:<28} {time.perf_counter() - began:8.2f} s", flush=True)
    return result


def main(cells):
    ns = runpy.run_path(str(EXAMPLE))
    backend = ns["MultistageBatteryBackend"]()
    view = ns["LazyBatteryDataView"](backend=backend)

    cell_iris = [f"Item:Cell{c}" for c in cells]
    proc_iris = sorted({backend._cell_procedure[c] for c in cell_iris})
    for iri in cell_iris:
        _time(f"load {iri}", backend._load, iri)

    view._selected_cell_ids = cell_iris
    view._selected_proc_ids = proc_iris
    _time("query", view._run_query)

    traces = view._resolve_traces()
    axes = {k: view._axis_map.get(k) for k in ("x", "y1", "y2")}
    print(f"axes: {axes}, traces: {len(traces)}")
    for axis, field in axes.items():
        if field:
            _time(f"convert {axis} ({field})", view._get_vals, traces, field)

    fig = _time("time-series figure", view._make_figure)
    points = sum(
        len(next(iter(r.data_source.data.values())))
        for r in fig.renderers
        if isinstance(r.data_source, ColumnDataSource)
    )
    print(f"renderers: {len(fig.renderers)}, points: {points}")
    payload = _time("serialize time-series", lambda: json.dumps(json_item(fig)))
    print(f"payload: {len(payload) / 1e6:.1f} MB")

    _time("cycle figure", view._build_cycle_figure)
    if hasattr(view, "_batch_col"):
        _time("refresh batches", view._refresh_batches)
        _time("batch figure", view._build_batch_figure)
    _time("_build_figure (all)", view._build_figure)


if __name__ == "__main__":
    main(sys.argv[1:] or ["1-1", "1-2"])
