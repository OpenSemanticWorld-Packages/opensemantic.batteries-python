"""Battery cycling time-series data model.

Reuses ``opensemantic.characteristics.quantitative.v1.TabularData`` (pint-pandas
``to_df()`` / ``from_df()``) and the typed quantity characteristics for the row
values. Row field names are unit-agnostic - the unit lives in the typed value -
so a column can hold any compatible unit.

JSON-LD export is deferred: emitting ``rows`` as ``Property:HasRow`` and each row
characteristic as ``Property:HasCharacteristic`` (with value/unit/type inherited
from ``QuantityValue``) needs nested/scoped ``@context`` composition that the
released ``oold`` does not yet support. See the upstream oold-python issue.
"""

from typing import List, Optional

from opensemantic.characteristics.quantitative.v1 import (
    Count,
    ElectricCharge,
    ElectricCurrent,
    Energy,
    TabularData,
    Time,
    Voltage,
)
from opensemantic.v1 import OswBaseModel


class CyclingDataRow(OswBaseModel):
    """One time-series record of a battery cycling test.

    Field names use the common battery-data semantics with the unit suffix
    stripped (the unit lives in the typed value). ``test_time``, ``voltage`` and
    ``current`` are the core columns; the cycle/step counters, step time,
    capacity and energy are present in all common cycler exports. Further columns
    flow through as auto-extended extra columns of ``TabularData.from_df`` (when
    their unit is registered).
    """

    test_time: Time
    voltage: Voltage
    current: ElectricCurrent
    cycle_count: Optional[Count] = None
    step_count: Optional[Count] = None
    step_time: Optional[Time] = None
    capacity: Optional[ElectricCharge] = None
    energy: Optional[Energy] = None


class BatteryCyclingDataset(TabularData):
    """A battery cycling dataset: an ordered list of ``CyclingDataRow``.

    Inherits ``to_df()`` / ``from_df()`` / JSON round-trip from ``TabularData``.
    """

    rows: List[CyclingDataRow]
