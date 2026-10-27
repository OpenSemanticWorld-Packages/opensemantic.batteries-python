"""Per-cycle summary table of a cycling dataset.

:class:`CycleDataRow` and :class:`CycleSummaryDataset` are temporary local
extensions: the world.opensemantic.batteries schema has no cycle table yet.
Once it ships one, regenerate the models and remove this module.

A summary has one row per cycle with the charge and the discharge capacity of
that cycle and the two coulomb efficiencies derived from them (wide form). :func:`cycle_summary` builds it from the rows of a
cycling dataset, using the ``step_type`` of each row to tell the charge step
from the discharge step.

Layer note (see CLAUDE.md): Pydantic **v1**, like the cycling classes.
"""

from typing import Dict, List, Optional, Union

from opensemantic.base.v1 import Dataset
from opensemantic.batteries._cycling import StepType
from opensemantic.characteristics.quantitative.v1 import (
    Count,
    Efficiency,
    ElectricCharge,
)
from opensemantic.core.v1 import Characteristic, Label
from pydantic.v1 import Field

__all__ = ["CycleDataRow", "CycleSummaryDataset", "cycle_summary"]


class CycleDataRow(Characteristic):
    """One row of a cycle summary: the capacities and efficiencies of a cycle.

    Temporary local extension until the world.opensemantic.batteries schema
    ships a cycle table; then drop this class.
    """

    cycle_count: Count = Field(..., description="Cycle number.")
    charge_capacity: Optional[ElectricCharge] = Field(
        None, description="Charge capacity of the cycle."
    )
    discharge_capacity: Optional[ElectricCharge] = Field(
        None, description="Discharge capacity of the cycle."
    )
    charge_coulomb_efficiency: Optional[Efficiency] = Field(
        None,
        description="Charge coulomb efficiency of the cycle: charge capacity "
        "divided by discharge capacity. Stored as a dimensionless ratio.",
    )
    discharge_coulomb_efficiency: Optional[Efficiency] = Field(
        None,
        description="Discharge coulomb efficiency of the cycle: discharge "
        "capacity divided by charge capacity. Stored as a dimensionless ratio.",
    )


class CycleSummaryDataset(Dataset):
    """A per-cycle summary dataset, with a default label for the frame conversion.

    Temporary local extension until the world.opensemantic.batteries schema
    ships a cycle table; then drop this class.
    """

    data: Optional[List[CycleDataRow]] = None

    @classmethod
    def from_df(
        cls,
        df,
        label: Optional[Union[str, List[Label]]] = "Cycle Summary",
        **kwargs,
    ):
        """Build a dataset from a pint-pandas frame.

        ``label`` is required by the model, so a default is supplied; pass a
        string or a list of ``Label``.
        """
        labels = [Label(text=label)] if isinstance(label, str) else label
        return super().from_df(df, label=labels, **kwargs)


def _in_coulomb(value: Optional[ElectricCharge]) -> Optional[float]:
    """The magnitude of a capacity in coulomb, or ``None`` if it is missing."""
    if value is None or value.value is None:
        return None
    return float(value.to_pint().to("coulomb").magnitude)


def cycle_summary(
    rows: List,
    label: Union[str, List[Label]] = "Cycle Summary",
) -> CycleSummaryDataset:
    """Summarize cycling rows into one capacity and efficiency record per cycle.

    Rows are grouped by ``cycle_count``; rows without one are skipped. The
    charge capacity of a cycle is the largest capacity of its ``CCCV_CHG``
    rows and the discharge capacity the largest of its ``CC_DCH`` rows, each
    converted to coulomb before the comparison. A capacity is ``None`` when
    the cycle has no such rows or they carry no capacity. Rows without a
    ``step_type`` count for neither. The result is sorted by cycle number.

    The discharge coulomb efficiency of a cycle is its discharge capacity
    divided by its charge capacity, and the charge coulomb efficiency is the
    inverse. Each is a dimensionless ratio, and ``None`` unless both
    capacities exist and the denominator is greater than zero.
    """
    charge: Dict[float, Optional[float]] = {}
    discharge: Dict[float, Optional[float]] = {}
    for row in rows:
        cycle = getattr(row, "cycle_count", None)
        if cycle is None or cycle.value is None:
            continue
        number = cycle.value
        charge.setdefault(number, None)
        discharge.setdefault(number, None)
        step = getattr(row, "step_type", None)
        target = (
            charge
            if step == StepType.CCCV_CHG
            else discharge
            if step == StepType.CC_DCH
            else None
        )
        if target is None:
            continue
        capacity = _in_coulomb(getattr(row, "capacity", None))
        if capacity is not None and (
            target[number] is None or capacity > target[number]
        ):
            target[number] = capacity

    def _value(capacity: Optional[float]) -> Optional[ElectricCharge]:
        return None if capacity is None else ElectricCharge(value=capacity)

    def _ratio(
        numerator: Optional[float], denominator: Optional[float]
    ) -> Optional[Efficiency]:
        if numerator is None or denominator is None or not denominator > 0:
            return None
        return Efficiency(value=numerator / denominator)

    summary_rows = [
        CycleDataRow(
            cycle_count=Count(value=number),
            charge_capacity=_value(charge[number]),
            discharge_capacity=_value(discharge[number]),
            charge_coulomb_efficiency=_ratio(charge[number], discharge[number]),
            discharge_coulomb_efficiency=_ratio(discharge[number], charge[number]),
        )
        for number in sorted(charge)
    ]
    labels = [Label(text=label)] if isinstance(label, str) else label
    return CycleSummaryDataset(label=labels, data=summary_rows)
