"""Abstract base for battery cycler file importers."""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Union

import pandas as pd

from .._dataset import BatteryCyclingDataset


class CyclerImporter(ABC):
    """Reads a cycler export file into a ``BatteryCyclingDataset``.

    Subclasses implement ``to_dataframe()`` to produce a pint-pandas DataFrame
    whose columns are the unit-stripped ``CyclingDataRow`` field names (with
    pint units attached); ``read()`` wraps that into a ``BatteryCyclingDataset``.
    """

    @abstractmethod
    def to_dataframe(self, path: Union[str, Path], **opts) -> pd.DataFrame:
        """Parse ``path`` into a pint-pandas DataFrame with stripped-key columns."""

    def read(self, path: Union[str, Path], **opts) -> BatteryCyclingDataset:
        """Parse ``path`` and return a ``BatteryCyclingDataset``."""
        return BatteryCyclingDataset.from_df(self.to_dataframe(path, **opts))
