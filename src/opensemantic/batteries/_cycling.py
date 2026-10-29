"""Cycling dataset controller.

``ElectrochemicalCyclingDataset`` and ``CyclingDataRow`` are the generated
models. The dataset already carries the DataFrame round-trip (``to_df`` /
``from_df``) and the distribution handling (``materialize_data`` /
``externalize_data``), because the ``Dataset`` it is generated from names the
controller that implements it; :class:`CyclingDatasetController` only supplies
the label a cycling dataset is built with.

``step_type`` is not yet part of the generated schema, so a temporary local
subclass of the row (:class:`StepTypedCyclingDataRow`) adds it and the
controller redeclares ``data`` with that row class.

Layer note (see CLAUDE.md): the cycling classes are Pydantic **v1**, matching
the ``opensemantic.characteristics.quantitative.v1`` values of the row fields.
Stay in the v1 layer here.
"""

from enum import Enum
from typing import List, Optional, Union

from pydantic.v1 import Field

from opensemantic.batteries.v1._model import (  # noqa: F401 (re-export)
    CyclingDataRow,
    ElectrochemicalCyclingDataset,
)
from opensemantic.core.v1 import Label

__all__ = [
    "CyclingDataRow",
    "ElectrochemicalCyclingDataset",
    "CyclingDatasetController",
    "StepType",
    "StepTypedCyclingDataRow",
]


class StepType(str, Enum):
    """Operational mode of a test step, as reported by the cycler software."""

    CC_CHG = "CC_CHG"
    CC_DCH = "CC_DCH"
    CV_CHG = "CV_CHG"
    CV_DCH = "CV_DCH"
    CCCV_CHG = "CCCV_CHG"
    CCCV_DCH = "CCCV_DCH"
    REST = "REST"
    OCV = "OCV"
    EIS = "EIS"
    OTHER = "OTHER"


class StepTypedCyclingDataRow(CyclingDataRow):
    """A cycling data row with the ``step_type`` field.

    Temporary local extension until the world.opensemantic.batteries schema
    ships ``step_type``; then drop this subclass.
    """

    step_type: Optional[StepType] = Field(
        None,
        description=(
            "A string label describing the operational mode of the current test "
            "step, as reported by the cycler software. Common values include "
            "CC_CHG (constant-current charge), CC_DCH (constant-current "
            "discharge), CV_CHG (constant-voltage charge), CV_DCH "
            "(constant-voltage discharge), CCCV_CHG (constant-current "
            "constant-voltage charge), CCCV_DCH (constant-current "
            "constant-voltage discharge), REST, OCV, and EIS."
        ),
    )


class CyclingDatasetController(ElectrochemicalCyclingDataset):
    """A cycling dataset, with a default label for the frame conversion."""

    data: Optional[List[StepTypedCyclingDataRow]] = None

    @classmethod
    def from_df(
        cls,
        df,
        label: Optional[Union[str, List[Label]]] = "Cycling Dataset",
        **kwargs,
    ):
        """Build a dataset from a pint-pandas frame.

        ``label`` is required by the model, so a default is supplied; pass a
        string or a list of ``Label``.
        """
        labels = [Label(text=label)] if isinstance(label, str) else label
        # An all-missing object column (e.g. ``step_type`` of rows that carry
        # none) makes the base ``from_df`` fail on ``series.pint`` (pandas
        # reports the accessor for it), and it holds no values to restore.
        empty = [c for c in df.columns if df[c].dtype == object and df[c].isna().all()]
        if empty:
            df = df.drop(columns=empty)
        return super().from_df(df, label=labels, **kwargs)
