"""Cycling dataset controller.

``ElectrochemicalCyclingDataset``, ``CyclingDataRow`` and the ``StepType``
enum of its ``step_type`` field are the generated models. The dataset already
carries the DataFrame round-trip (``to_df`` / ``from_df``) and the
distribution handling (``materialize_data`` / ``externalize_data``), because
the ``Dataset`` it is generated from names the controller that implements it;
:class:`CyclingDatasetController` only supplies the label a cycling dataset is
built with.

Layer note (see CLAUDE.md): the cycling classes are Pydantic **v1**, matching
the ``opensemantic.characteristics.quantitative.v1`` values of the row fields.
Stay in the v1 layer here.
"""

from typing import List, Optional, Union

from opensemantic.batteries.v1._model import (  # noqa: F401 (re-export)
    CyclingDataRow,
    ElectrochemicalCyclingDataset,
    StepType,
)
from opensemantic.core.v1 import Label

__all__ = [
    "CyclingDataRow",
    "ElectrochemicalCyclingDataset",
    "CyclingDatasetController",
    "StepType",
]


class CyclingDatasetController(ElectrochemicalCyclingDataset):
    """A cycling dataset, with a default label for the frame conversion."""

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
