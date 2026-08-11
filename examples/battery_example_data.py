"""Minimal cycling-data example following schema_addon conventions.

Cell and procedure classes are imported from ``opensemantic.batteries.v1`` where
they already exist (``BatteryCell``, ``ElectrochemicalTestProcedure`` and its
``AgingTestProcedure`` / ``FormationTestProcedure`` subclasses). Only the
example-specific test run, dataset and cell form-factor classes are defined here.
"""

from __future__ import annotations
import random

from typing import List, Optional

from opensemantic.batteries.v1 import (
    AgingTestProcedure,
    BatteryCell,
    ElectrochemicalTestProcedure,
    FormationTestProcedure,
)
from opensemantic.characteristics.quantitative.v1 import (
    Characteristic,
    ElectricCurrent,
    Time,
    Voltage,
)
from opensemantic.core.v1 import Item, Label
from opensemantic.lab.v1 import AnalyticalLaboratoryProcess


# ---------------------------------------------------------------------------
# Schema definitions
# ---------------------------------------------------------------------------

class ElectrochemicalCyclingDataRow(Characteristic):
    """One row of a cycling measurement (structured value, no label needed)."""
    test_time: Time = None
    voltage: Voltage = None
    current: ElectricCurrent = None


class ElectrochemicalCyclingDataset(Item):
    """Tabular dataset of cycling rows.

    Inherits from Item (information artifact on the dataspace).
    `data` is empty on the dataspace; populated in memory after deserialization.
    """
    data: List[ElectrochemicalCyclingDataRow] = []


class ElectrochemicalTest(AnalyticalLaboratoryProcess):
    """One run of an electrochemical test on one or more cells.

    device_under_test  inherited from AnalyticalLaboratoryProcess
    protocol           the procedure instance followed (AgingTestProcedure, …)
    output             the resulting cycling dataset
    """
    # device_under_test: list[PhysicalItem] already on AnalyticalLaboratoryProcess
    protocol: Optional[ElectrochemicalTestProcedure] = None
    output: Optional[ElectrochemicalCyclingDataset] = None


class CylindricalCell(BatteryCell):
    pass


class PrismaticCell(BatteryCell):
    pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sample_cycling_data() -> List[ElectrochemicalCyclingDataRow]:
    return [
        ElectrochemicalCyclingDataRow(test_time=Time(value=0.0), voltage=Voltage(value=3.0 + random.uniform(-0.3, 0.3)), current=ElectricCurrent(value=0.0+ random.uniform(-0.1, 0.1))),
        ElectrochemicalCyclingDataRow(test_time=Time(value=1.0), voltage=Voltage(value=3.1 + random.uniform(-0.3, 0.3)), current=ElectricCurrent(value=0.5+ random.uniform(-0.1, 0.1))),
        ElectrochemicalCyclingDataRow(test_time=Time(value=2.0), voltage=Voltage(value=3.2 + random.uniform(-0.3, 0.3)), current=ElectricCurrent(value=0.5+ random.uniform(-0.1, 0.1))),
        ElectrochemicalCyclingDataRow(test_time=Time(value=3.0), voltage=Voltage(value=3.3 + random.uniform(-0.3, 0.3)), current=ElectricCurrent(value=0.5+ random.uniform(-0.1, 0.1))),
        ElectrochemicalCyclingDataRow(test_time=Time(value=4.0), voltage=Voltage(value=3.1 + random.uniform(-0.3, 0.3)), current=ElectricCurrent(value=0.0+ random.uniform(-0.1, 0.1))),
    ]


def _make_dataset(name: str) -> ElectrochemicalCyclingDataset:
    return ElectrochemicalCyclingDataset(
        label=[Label(text=name)],
        data=_sample_cycling_data(),
    )


# ---------------------------------------------------------------------------
# Cells
# ---------------------------------------------------------------------------

cell_a = CylindricalCell(label=[Label(text="Cell A")])
cell_b = CylindricalCell(label=[Label(text="Cell B")])
cell_c = PrismaticCell(label=[Label(text="Cell C")])


# ---------------------------------------------------------------------------
# Procedures (protocol instances)
# ---------------------------------------------------------------------------

aging_test_a = AgingTestProcedure(label=[Label(text="Aging Test A")])
aging_test_b = AgingTestProcedure(label=[Label(text="Aging Test B")])
formation_procedure = FormationTestProcedure(label=[Label(text="Formation Test")])


# ---------------------------------------------------------------------------
# Test runs
# ---------------------------------------------------------------------------

# Cell A: AgingTestA + Formation
test_cell_a_aging_a = ElectrochemicalTest(
    label=[Label(text="Cell A - Aging (A)")],
    device_under_test=[cell_a],
    protocol=aging_test_a,
    output=_make_dataset("Cell A - Aging (A) Dataset"),
)

test_cell_a_formation = ElectrochemicalTest(
    label=[Label(text="Cell A - Formation")],
    device_under_test=[cell_a],
    protocol=formation_procedure,
    output=_make_dataset("Cell A - Formation Dataset"),
)

# Cell B: AgingTestA + AgingTestB + Formation
test_cell_b_aging_a = ElectrochemicalTest(
    label=[Label(text="Cell B - Aging (A)")],
    device_under_test=[cell_b],
    protocol=aging_test_a,
    output=_make_dataset("Cell B - Aging (A) Dataset"),
)

test_cell_b_aging_b = ElectrochemicalTest(
    label=[Label(text="Cell B - Aging (B)")],
    device_under_test=[cell_b],
    protocol=aging_test_b,
    output=_make_dataset("Cell B - Aging (B) Dataset"),
)

test_cell_b_formation = ElectrochemicalTest(
    label=[Label(text="Cell B - Formation")],
    device_under_test=[cell_b],
    protocol=formation_procedure,
    output=_make_dataset("Cell B - Formation Dataset"),
)

# Cell C: Formation only
test_cell_c_formation = ElectrochemicalTest(
    label=[Label(text="Cell C - Formation")],
    device_under_test=[cell_c],
    protocol=formation_procedure,
    output=_make_dataset("Cell C - Formation Dataset"),
)
