"""Abstract base for battery cycler file importers."""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Union

import pandas as pd

from .._cycling import ElectrochemicalCyclingDataset, dataset_from_df


class CyclerImporter(ABC):
    """Reads a cycler export file into an ``ElectrochemicalCyclingDataset``.

    Subclasses implement ``to_dataframe()`` to produce a pint-pandas DataFrame
    whose columns are the unit-stripped ``CyclingDataRow`` field names (with pint
    units attached); ``read()`` wraps that into an ``ElectrochemicalCyclingDataset``
    (labelled with the source file name).
    """

    @abstractmethod
    def to_dataframe(self, path: Union[str, Path], **opts) -> pd.DataFrame:
        """Parse ``path`` into a pint-pandas DataFrame with stripped-key columns."""

    def read(self, path: Union[str, Path], **opts) -> ElectrochemicalCyclingDataset:
        """Parse ``path`` and return an ``ElectrochemicalCyclingDataset``."""
        df = self.to_dataframe(path, **opts)
        return dataset_from_df(df, label=Path(path).name)
