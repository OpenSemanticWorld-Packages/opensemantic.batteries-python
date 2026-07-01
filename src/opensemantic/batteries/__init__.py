from importlib.metadata import PackageNotFoundError, version  # pragma: no cover

try:
    # Change here if project is renamed and does not equal the package name
    dist_name = "opensemantic.batteries"
    __version__ = version(dist_name)
except PackageNotFoundError:  # pragma: no cover
    __version__ = "unknown"
finally:
    del version, PackageNotFoundError

from opensemantic.batteries._model import *  # noqa

try:
    from opensemantic.batteries._controller import *  # noqa
except ImportError:
    pass

from opensemantic.batteries._dataset import (  # noqa: F401
    BatteryCyclingDataset,
    CyclingDataRow,
)
from opensemantic.batteries._importers import (  # noqa: F401
    CyclerImporter,
    MaccorImporter,
    read_maccor,
)
