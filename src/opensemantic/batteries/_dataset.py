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

import pandas as pd
import pint_pandas
from opensemantic.characteristics.quantitative.v1 import (
    Count,
    ElectricCharge,
    ElectricCurrent,
    Energy,
    TabularData,
    Time,
    Voltage,
    Pressure,
    Temperature,
    Power
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
    power: Optional[Power] = None


class BatteryCyclingDataset(TabularData):
    """A battery cycling dataset: an ordered list of ``CyclingDataRow``.

    Inherits ``to_df()`` / ``from_df()`` / JSON round-trip from ``TabularData``.
    """

    rows: List[CyclingDataRow]


class AirQualityDataRow(OswBaseModel):
    """
    """
    test_time: Time
    temperature: Temperature
    pressure: Pressure


class AirQualityDataset(TabularData):
    """
    Inherits ``to_df()`` / ``from_df()`` / JSON round-trip from ``TabularData``.
    """
    rows: List[AirQualityDataRow]


class NoramlizedDataRow(OswBaseModel):
    #todo generete this dynamically
    test_time: Time
    temperature: Temperature
    pressure: Pressure

    voltage: Voltage
    current: ElectricCurrent
    cycle_count: Optional[Count] = None
    step_count: Optional[Count] = None
    step_time: Optional[Time] = None
    capacity: Optional[ElectricCharge] = None
    energy: Optional[Energy] = None
    power: Optional[Power] = None


class NormalizedDataset(TabularData):
    """
    Inherits ``to_df()`` / ``from_df()`` / JSON round-trip from ``TabularData``.
    """
    rows: List[NoramlizedDataRow]

def interpolate_pint_columns(df, merge_on):
    df = df.sort_values(merge_on)

    pint_columns = {}

    # Convert Pint columns to base units and extract magnitudes
    for col in df.columns:
        if isinstance(df[col].dtype, pint_pandas.PintType):
            base = df[col].pint.to_base_units()

            pint_columns[col] = base.pint.units

            # keep only numeric magnitudes for interpolation
            df[col] = base.pint.magnitude

    # Interpolate numeric data
    df = df.interpolate(method="linear")

    # Restore PintArray columns
    for col, unit in pint_columns.items():
        df[col] = pd.Series(
            pd.array(df[col], dtype=f"pint[{unit}]"),
            index=df.index
        )

    return df

def merge_tabular_data(tabular_data1 : TabularData,  tabular_data2: TabularData, merge_on = "test_time") -> TabularData:

    df1 = tabular_data1.to_df()
    df2 = tabular_data2.to_df()

    combined_df = pd.merge(df1, df2, on=merge_on, how="outer")
    combined_df = combined_df.sort_values(merge_on)
    combined_df = combined_df.set_index(merge_on)
    combined_df = interpolate_pint_columns(combined_df, merge_on)
    # combined_df = combined_df.interpolate(method="linear")
    combined_df = combined_df.reset_index()

    combined_df.to_csv("test.csv")

    return NormalizedDataset.from_df(combined_df)



