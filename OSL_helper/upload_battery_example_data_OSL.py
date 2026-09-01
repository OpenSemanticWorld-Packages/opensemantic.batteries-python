"""Minimal cycling-data example following schema_addon conventions.

Cell and procedure classes are imported from ``opensemantic.batteries.v1`` where
they already exist (``BatteryCell``, ``ElectrochemicalTestProcedure`` and its
``AgingTestProcedure`` / ``FormationTestProcedure`` subclasses). Only the
example-specific test run, dataset and cell form-factor classes are defined here.
"""

from __future__ import annotations
import random
from pathlib import Path

from typing import List, Optional

from opensemantic.batteries.v1 import (
    AgingTestProcedure,
    BatteryCell,
    ElectrochemicalTestProcedure,
    FormationTestProcedure, TestProcedureItem,ElectrochemicalTest
)
from opensemantic.characteristics.quantitative.v1 import (
    Characteristic,
    ElectricCurrent,
    Time,
    Voltage, Count, ElectricCharge,
)
from opensemantic.core.v1 import Item, Label
from opensemantic.lab.v1 import AnalyticalLaboratoryProcess


from osw.defaults import params as default_params
from osw.defaults import paths as default_paths
from osw.express import OswExpress

default_paths.cred_filepath = Path(r"../examples/accounts.pwd.yaml")
default_params.wiki_domain = "wiki-dev.open-semantic-lab.org"
wiki_domain = "wiki-dev.open-semantic-lab.org"

osw_obj = OswExpress(domain=wiki_domain, cred_filepath=default_paths.cred_filepath)


dependencies = {


"CyclingDataRow" : "Category:OSW52787b16dd264707a2d2af4a3b866936",## row
# "ElectrochemicalCyclingDataRow" : "Category:OSW52787b16dd264707a2d2af4a3b866936",
"ElectrochemicalCyclingDataset" : "Category:OSW5af2a0c1f6a848b591678b2473674a49", #uses row in data
# "ElectrochemicalTest" : "Category:OSW6f39d77241e24a33ab6d036dfac03ace", ## derived from analytical laboratiry process, can ElectrochemicalCyclingDataset be used in output?? -> is packaged
"CylindricalCell" : "Category:OSWf80456d65087488fb202f72f031d9df4",
"PrismaticCell" : "Category:OSW3d1616266eea400aa0cdae0e1d8cfead"
}


# Will run everytime the script is executed, uncomment if installed
# osw_obj.install_dependencies(dependencies,mode = "replace")

from osw.model.entity import CylindricalCell, PrismaticCell, ElectrochemicalCyclingDataset,CyclingDataRow

# ---------------------------------------------------------------------------
# Cells
# ---------------------------------------------------------------------------

cell_a = "Item:OSW7bae5d74c11842fc8fdc5f12d264a5f1"
cell_b = "Item:OSW4a20efb16be64868ab9d16a97838434a"
cell_c = "Item:OSW35ff60500092495ba72d0624f830129b"

# ---------------------------------------------------------------------------
# Procedures (protocol instances)
# ---------------------------------------------------------------------------


aging_test_a = "Item:OSW365966aaa8d64804b5ff0351c9db5382"
aging_test_b = "Item:OSW606b66a2c1a94f8c86c3821807cf9bff"
formation_procedure = "Item:OSWecce41274e5b403a9de4179b04b49a1e"

# ``TestProcedureItem.test_procedure_instance`` is a string instance reference
# (an OSW IRI like ``Item:OSW…``), not the procedure object — so link each
# procedure by its ``get_iri()``. The dashboard resolves the IRI back to the
# procedure object via its procedure_objects map (see BatteryDataView).
test_procedure_aging_test_a = [TestProcedureItem(test_procedure_subcategory= "Category:OSWdda41d4a4ec0421babe0295c6edcb5df",
                            test_procedure_instance= aging_test_a,
                            test_procedure_instance_property = "Property:HasProcedure")]


test_procedure_aging_test_b = [TestProcedureItem(test_procedure_subcategory= "Category:OSWdda41d4a4ec0421babe0295c6edcb5df",
                            test_procedure_instance= aging_test_b,
                            test_procedure_instance_property = "Property:HasProcedure")]


test_procedure_formation_procedure = [TestProcedureItem(test_procedure_subcategory= "Category:OSWdda41d4a4ec0421babe0295c6edcb5df",
                            test_procedure_instance= formation_procedure,
                            test_procedure_instance_property = "Property:HasProcedure")]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sample_cycling_data() -> List[CyclingDataRow]:
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

    rows: List[CyclingDataRow] = []
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
        rows.append(CyclingDataRow(
            test_time=Time(value=t),
            voltage=Voltage(value=v + random.uniform(-0.3, 0.3)),
            current=ElectricCurrent(value=current),
            cycle_count=Count(value=cycle),
            capacity=ElectricCharge(value=round(capacity, 4)),
        ))
    return rows


def _make_dataset(name: str) -> List[ElectrochemicalCyclingDataset]:
    dataset = ElectrochemicalCyclingDataset(
        label=[Label(text=name)],
        data=_sample_cycling_data(),
    )
    osw_obj.store_entity(dataset)
    return [dataset]


# ---------------------------------------------------------------------------
# Test runs
# ---------------------------------------------------------------------------

# Cell A: AgingTestA + Formation
# test_cell_a_aging_a = ElectrochemicalTest(
#     label=[Label(text="Cell A - Aging (A)")],
#     device_under_test=[cell_a],
#     test_procedure=test_procedure_aging_test_a,
#     output=_make_dataset("Cell A - Aging (A) Dataset"),
# )
#
#
# test_cell_a_aging_a = "Item:OSW3b8adbb8c9ce4ac7ae89d30de43a1d05"


test_cell_a_formation = ElectrochemicalTest(
    label=[Label(text="Cell A - Formation")],
    device_under_test=[cell_a],
    test_procedure=test_procedure_formation_procedure,
    output=_make_dataset("Cell A - Formation Dataset"),
)
osw_obj.store_entity(test_cell_a_formation)
test_cell_a_formation ="Item:OSW938e6a74e85a47b0b5a2a355e2ce6b94"

# Cell B: AgingTestA + AgingTestB + Formation
test_cell_b_aging_a = ElectrochemicalTest(
    label=[Label(text="Cell B - Aging (A)")],
    device_under_test=[cell_b],
    test_procedure=test_procedure_aging_test_a,
    output=_make_dataset("Cell B - Aging (A) Dataset"),
)
osw_obj.store_entity(test_cell_b_aging_a)
test_cell_b_aging_a = "Item:OSW40d0053068a8495fbbe8526b20f4d7e9"

test_cell_b_aging_b = ElectrochemicalTest(
    label=[Label(text="Cell B - Aging (B)")],
    device_under_test=[cell_b],
    test_procedure=test_procedure_aging_test_b,
    output=_make_dataset("Cell B - Aging (B) Dataset"),
)
osw_obj.store_entity(test_cell_b_aging_b)
test_cell_b_aging_b = "Item:OSW30313ec0213a42eeb3033e24583cb0d4"

test_cell_b_formation = ElectrochemicalTest(
    label=[Label(text="Cell B - Formation")],
    device_under_test=[cell_b],
    test_procedure=test_procedure_formation_procedure,
    output=_make_dataset("Cell B - Formation Dataset"),
)
osw_obj.store_entity(test_cell_b_formation)
test_cell_b_formation = "Item:OSW1abc2aa549cf496c9a1c6bd3f5728717"
# Cell C: Formation only
test_cell_c_formation = ElectrochemicalTest(
    label=[Label(text="Cell C - Formation")],
    device_under_test=[cell_c],
    test_procedure=test_procedure_formation_procedure,
    output=_make_dataset("Cell C - Formation Dataset"),
)
osw_obj.store_entity(test_cell_c_formation)
test_cell_c_formation = "Item:OSW2beded327c644d2e9cb2352a3f9eecac"



