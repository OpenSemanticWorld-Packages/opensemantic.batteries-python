"""Battery view UI components.

Battery cycling dashboard and the generic OO-LD tree builder used to drive
its cell / procedure trees. Shared dashboard infrastructure (BaseDataView,
config, unit helpers) lives in ``opensemantic.base.view``.
"""

from opensemantic.batteries.view._backend import (
    BatteryDataBackend,
    Dataset,
    TreeNode,
)
from opensemantic.batteries.view._battery_dashboard import BatteryDataView
from opensemantic.batteries.view._battery_utils import (
    build_oold_tree_source,
    get_checked_instance_ids,
    inject_axis_children,
)
from opensemantic.batteries.view._lazy_dashboard import LazyBatteryDataView
from opensemantic.batteries.view._oold_tree import (
    LazySource,
    OOLDTreeBuilder,
    PythonSource,
    RelationSpec,
    field_rel,
    has_type,
)
from opensemantic.batteries.view._osl_backend import (
    OSLBatteryBackend,
    connect_osw,
)

__all__ = [
    "BatteryDataView",
    "LazyBatteryDataView",
    "BatteryDataBackend",
    "TreeNode",
    "Dataset",
    "OSLBatteryBackend",
    "connect_osw",
    "OOLDTreeBuilder",
    "PythonSource",
    "LazySource",
    "RelationSpec",
    "has_type",
    "field_rel",
    "build_oold_tree_source",
    "get_checked_instance_ids",
    "inject_axis_children",
]
