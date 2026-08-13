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
    Voltage, Count, ElectricCharge,
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

    cycle_count: Optional[Count] = None
    step_count: Optional[Count] = None
    step_time: Optional[Time] = None
    capacity: Optional[ElectricCharge] = None


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
    """Five rows of a toy charge profile spanning two cycles.

    ``cycle_count`` rolls over from 0 to 1 at the middle row, and ``capacity``
    accumulates the charge passed (``Q += I·Δt``) within each cycle, resetting to
    zero when the new cycle starts — so the two optional columns stay physically
    sensible.
    """
    # (test_time [h], voltage [V], current [A]) base profile.
    base = [
        (0.0, 3.0, 0.0),
        (1.0, 3.1, 0.5),
        (2.0, 3.2, 0.5),
        (3.0, 3.3, 0.5),
        (4.0, 3.1, 0.0),
    ]
    mid = len(base) // 2  # cycle count increases 0 -> 1 in the middle

    rows: List[ElectrochemicalCyclingDataRow] = []
    capacity = 0.0
    prev_cycle = 0
    prev_time = 0.0
    for i, (t, v, c) in enumerate(base):
        cycle = 0 if i < mid else 1
        if cycle != prev_cycle:
            capacity = 0.0  # new cycle: accumulated charge restarts
            prev_cycle = cycle
        current = c + random.uniform(-0.1, 0.1)
        capacity += max(current, 0.0) * (t - prev_time)  # Q += I·Δt (Ah)
        prev_time = t
        rows.append(ElectrochemicalCyclingDataRow(
            test_time=Time(value=t),
            voltage=Voltage(value=v + random.uniform(-0.3, 0.3)),
            current=ElectricCurrent(value=current),
            cycle_count=Count(value=cycle),
            capacity=ElectricCharge(value=round(capacity, 4)),
        ))
    return rows


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
