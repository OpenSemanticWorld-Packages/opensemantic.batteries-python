"""Maccor cycler import via the optional ``maccor-utility`` package.

``maccor-utility`` parses the proprietary text/MIMS export formats (decimal
comma, ``0d 00:00:0`` test-time, ...) and ``rename_columns(..., target=raw)``
canonicalises the column names. This importer maps those canonical names to the
unit-stripped ``CyclingDataRow`` field names and attaches pint units, then hands
the DataFrame to ``dataset_from_df`` (via ``CyclerImporter.read``).
"""

from pathlib import Path
from typing import Dict, Optional, Tuple, Union

import pandas as pd

from .._cycling import ElectrochemicalCyclingDataset
from .base import CyclerImporter

# canonical (raw) Maccor column -> (stripped field name, pint unit string)
MACCOR_TO_FIELD: Dict[str, Tuple[str, str]] = {
    "TestTime": ("test_time", "second"),
    "Voltage": ("voltage", "volt"),
    "Current": ("current", "ampere"),
    "CycleNumProc": ("cycle_count", "dimensionless"),
    "StepNum": ("step_count", "dimensionless"),
    "StepTime": ("step_time", "second"),
    "Capacity": ("capacity", "ampere_hour"),
    "Energy": ("energy", "watt_hour"),
}

# filename hint -> maccor-utility format key
_FORMAT_BY_HINT = {
    "export1": "maccor_export1",
    "export2": "maccor_export2",
    "mims_client1": "mims_client1",
    "mims_client2": "mims_client2",
    "mims_server2": "mims_server2",
}


class MaccorImporter(CyclerImporter):
    """Importer for Maccor text / MIMS export files."""

    def __init__(self, fmt: Optional[str] = None):
        # fmt: a maccor_utility.read.MaccorDataFormat, its name, or None to
        # auto-detect from the filename.
        self.fmt = fmt

    @staticmethod
    def _resolve_format(path: Union[str, Path], fmt):
        from maccor_utility.read import MaccorDataFormat

        if fmt is not None:
            if isinstance(fmt, MaccorDataFormat):
                return fmt
            return MaccorDataFormat[fmt]
        name = Path(path).name.lower()
        for hint, key in _FORMAT_BY_HINT.items():
            if hint in name:
                return MaccorDataFormat[key]
        raise ValueError(
            f"Could not detect the Maccor format from filename '{name}'. "
            f"Pass fmt= one of {list(_FORMAT_BY_HINT.values())}."
        )

    def to_dataframe(self, path: Union[str, Path], fmt=None, **opts) -> pd.DataFrame:
        try:
            from maccor_utility.read import (
                MaccorDataFormat,
                read_maccor_data_file,
                rename_columns,
            )
        except ImportError as e:  # pragma: no cover - optional dependency
            raise ImportError(
                "The Maccor importer requires the optional 'maccor-utility' "
                "package. Install it with: "
                "pip install opensemantic.batteries[maccor]"
            ) from e

        data_format = self._resolve_format(path, fmt if fmt is not None else self.fmt)
        result = read_maccor_data_file(str(path), data_format)
        raw = result.data.as_dataframe
        canonical = rename_columns(raw.copy(), data_format, MaccorDataFormat.raw)

        columns: Dict[str, pd.Series] = {}
        for src, (field, unit) in MACCOR_TO_FIELD.items():
            if src in canonical.columns:
                # maccor-utility already parses duration time columns to seconds
                values = pd.to_numeric(canonical[src], errors="coerce").astype(float)
                columns[field] = pd.Series(values.values, dtype=f"pint[{unit}]")
        return pd.DataFrame(columns)


def read_maccor(
    path: Union[str, Path], fmt: Optional[str] = None
) -> ElectrochemicalCyclingDataset:
    """Read a Maccor export file into an ``ElectrochemicalCyclingDataset``.

    ``fmt`` is the maccor-utility format (or its name); if omitted it is
    detected from the filename.
    """
    return MaccorImporter(fmt=fmt).read(path)
