"""Runtime cycling-dataset helpers.

Replaces the retired ``_dataset`` module. The dataset and row classes are the
OSW-generated ``ElectrochemicalCyclingDataset`` / ``CyclingDataRow``; the
DataFrame round-trip that ``_dataset.TabularData`` used to provide lives here as
plain runtime functions (:func:`dataset_to_df` / :func:`dataset_from_df`), since
the generated classes carry no pint/pandas logic of their own.

TEMP import source
------------------
``ElectrochemicalCyclingDataset`` and ``CyclingDataRow`` are imported from
``osw.model.entity`` for now. They will move into
``opensemantic.batteries._model`` / ``.v1._model`` once the schema package ships
them; when that happens, only the import below has to change.

Layer note (see CLAUDE.md): the generated classes are Pydantic **v1**, matching
the ``opensemantic.characteristics.quantitative.v1`` values used for the row
fields. Stay in the v1 layer here.
"""

from typing import List, Optional, Union

import pandas as pd

from opensemantic.characteristics.quantitative.v1 import QuantityValue
from opensemantic.characteristics.quantitative.v1._static import ureg
from opensemantic.core.v1 import Label

# TEMP: sourced from osw.model.entity until moved into
# opensemantic.batteries._model / .v1._model. Change only this import then.
from osw.model.entity import (  # noqa: F401
    CyclingDataRow,
    ElectrochemicalCyclingDataset,
)

__all__ = [
    "CyclingDataRow",
    "ElectrochemicalCyclingDataset",
    "dataset_to_df",
    "dataset_from_df",
]

# The row model whose fields define the tabular columns. Every field is a typed
# quantity value (``test_time``, ``voltage``, ...); ``type`` is the OSW category
# marker and is not a data column.
_ROW_CLASS = CyclingDataRow


def _value_fields(row_class) -> List[str]:
    """Data-column field names of a cycling row model (all but ``type``)."""
    return [f for f in row_class.__fields__ if f != "type"]


def _default_unit_str(row_class, field: str) -> str:
    """pint-compatible unit string for a row field's default unit."""
    unit_default = row_class.__fields__[field].type_.__fields__["unit"].default
    return QuantityValue.get_pint_ureg_compatible_str(unit_default.name)


def dataset_to_df(dataset: ElectrochemicalCyclingDataset) -> pd.DataFrame:
    """Return the dataset's rows as a pint-pandas DataFrame.

    One column per :class:`CyclingDataRow` data field, each carrying its default
    unit; missing optional values become ``NA``. Mirrors the old
    ``BatteryCyclingDataset.to_df``.
    """
    rows = dataset.data or []
    series = []
    for attr in _value_fields(_ROW_CLASS):
        target = _default_unit_str(_ROW_CLASS, attr)
        magnitudes = []
        for row in rows:
            value = getattr(row, attr, None)
            if value is None:
                magnitudes.append(None)
                continue
            source = QuantityValue.get_pint_ureg_compatible_str(value.unit.name)
            magnitudes.append((value.value * ureg(source)).to(target).magnitude)
        series.append(pd.Series(magnitudes, dtype=f"pint[{target}]", name=attr))
    return pd.DataFrame({s.name: s for s in series})


def dataset_from_df(
    df: pd.DataFrame,
    label: Optional[Union[str, List[Label]]] = "Cycling Dataset",
) -> ElectrochemicalCyclingDataset:
    """Build an :class:`ElectrochemicalCyclingDataset` from a pint-pandas frame.

    Columns matching a :class:`CyclingDataRow` field are converted to that
    field's default unit and stored; unknown columns are ignored. Mirrors the
    old ``BatteryCyclingDataset.from_df`` for the fixed row schema (it does not
    auto-extend the row model with extra columns).

    ``label`` is required by the generated dataset model, so a value is always
    supplied; pass a string or a list of ``Label``.
    """
    known = set(_value_fields(_ROW_CLASS))
    columns = [c for c in df.columns if c in known]

    # Convert every known column to its field's default unit up front.
    magnitudes = {
        attr: df[attr].pint.to(_default_unit_str(_ROW_CLASS, attr)).pint.magnitude
        for attr in columns
    }

    rows = []
    for i in range(len(df)):
        values = {}
        for attr in columns:
            mag = magnitudes[attr].iloc[i]
            if pd.isna(mag):
                continue
            values[attr] = {"value": float(mag)}
        rows.append(_ROW_CLASS(**values))

    labels = [Label(text=label)] if isinstance(label, str) else label
    return ElectrochemicalCyclingDataset(label=labels, data=rows)
