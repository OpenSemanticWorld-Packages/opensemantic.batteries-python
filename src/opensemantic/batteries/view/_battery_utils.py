"""Utilities for the battery cycling data view.

build_oold_tree_source   — convert an OO-LD SubClassOf/HasType graph into
                           a Wunderbaum-compatible tree source list.
inject_axis_children     — add x/y1/y2 axis-selection subtrees as children
                           of every instance node in-place.
get_checked_instance_ids — recursively collect node_ids of checked instance nodes,
                           skipping axis_group / axis_row synthetic nodes.
"""

from __future__ import annotations

from typing import Dict, List, Optional


def build_oold_tree_source(
    nodes: Dict[str, Dict],
    edges: List[Dict],
    root_keys: Optional[List[str]] = None,
) -> List[Dict]:
    """Convert an OO-LD graph to a Wunderbaum tree source.

    Edge convention (same as panelini dag_projection.py):
      SubClassOf  from=child,    to=parent  (CylindricalCell SubClassOf BatteryCell)
      HasType     from=instance, to=class   (cell_a HasType CylindricalCell)

    Each tree node carries:
      data.node_id  — the key from the ``nodes`` dict
      data.kind     — "class" | "instance"

    ``checkbox: True`` and ``selected: False`` are set on every node so
    Wunderbaum renders and syncs checkboxes correctly.
    """
    children_map: Dict[str, List[str]] = {}
    has_parent: set = set()

    for edge in edges:
        rel = edge.get("relation", "")
        if rel not in ("SubClassOf", "HasType"):
            continue
        parent_id = edge["to"]
        child_id = edge["from"]
        children_map.setdefault(parent_id, []).append(child_id)
        has_parent.add(child_id)

    if root_keys is None:
        root_keys = [k for k in nodes if k not in has_parent]

    def build_node(node_id: str, path: str) -> Dict:
        props = nodes.get(node_id, {})
        key = f"{path}/{node_id}" if path else node_id
        node: Dict = {
            "title": props.get("label", node_id),
            "key": key,
            "checkbox": True,
            "selected": False,
            "expanded": True,
            "data": {
                "node_id": node_id,
                "kind": props.get("kind", "class"),
            },
        }
        oold_children = children_map.get(node_id, [])
        if oold_children:
            node["children"] = [build_node(cid, key) for cid in oold_children]
        return node

    return [build_node(r, "") for r in root_keys]


def inject_axis_children(
    source: List[Dict],
    field_names: List[str],
    axis_map: Dict[str, Optional[str]],
) -> None:
    """Mutate *source* in-place: append x/y1/y2 axis subtrees to every instance node.

    Each instance node gets three axis-group children (x, y1, y2).
    Each axis-group has one child per field, with ``checkbox: True``
    and ``selected`` reflecting the current *axis_map*.

    Axis-group nodes carry ``data.kind = "axis_group"``; field rows carry
    ``data.kind = "axis_row"`` with ``data.axis`` and ``data.field``.

    Existing axis children (from a previous call) are replaced so the
    function is idempotent — call it again after changing *axis_map* to
    sync the tree state.
    """
    axes = ["x", "y1", "y2"]

    def _axis_subtree(instance_key: str) -> List[Dict]:
        groups: List[Dict] = []
        for axis in axes:
            rows: List[Dict] = []
            for field in field_names:
                rows.append({
                    "title": field,
                    "key": f"{instance_key}/__axis__/{axis}/{field}",
                    "checkbox": True,
                    "selected": axis_map.get(axis) == field,
                    "expanded": True,
                    "data": {"kind": "axis_row", "axis": axis, "field": field},
                })
            groups.append({
                "title": f"({axis})",
                "key": f"{instance_key}/__axis__/{axis}",
                "checkbox": False,
                "selected": False,
                "expanded": True,
                "data": {"kind": "axis_group", "axis": axis},
                "children": rows,
            })
        return groups

    def walk(nodes: List[Dict]) -> None:
        for node in nodes:
            kind = node.get("data", {}).get("kind", "")
            if kind in ("axis_group", "axis_row"):
                continue
            # Recurse into OO-LD children first
            oold_children = [
                c for c in node.get("children", [])
                if c.get("data", {}).get("kind") not in ("axis_group", "axis_row")
            ]
            walk(oold_children)
            if kind == "instance":
                node["children"] = oold_children + _axis_subtree(node["key"])

    walk(source)


def get_checked_instance_ids(source: List[Dict]) -> List[str]:
    """Recursively collect node_ids of checked nodes with kind='instance'.

    Skips axis_group and axis_row nodes (injected by inject_axis_children).
    """
    result: List[str] = []

    def walk(nodes: List[Dict]) -> None:
        for n in nodes:
            data = n.get("data", {})
            kind = data.get("kind", "")
            if kind in ("axis_group", "axis_row"):
                continue
            if n.get("selected") and kind == "instance":
                node_id = data.get("node_id")
                if node_id:
                    result.append(node_id)
            walk(n.get("children", []))

    walk(source)
    return result
