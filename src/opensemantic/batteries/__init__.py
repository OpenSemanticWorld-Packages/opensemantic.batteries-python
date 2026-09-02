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

# Cycling dataset + importers. The dataset/row classes are currently sourced
# from osw.model.entity (see _cycling.py) and require that package's generated
# entity module to be present; guard the import so the base package still loads
# without it.
try:
    from opensemantic.batteries._cycling import (  # noqa: F401
        CyclingDataRow,
        ElectrochemicalCyclingDataset,
        dataset_from_df,
        dataset_to_df,
    )
    from opensemantic.batteries._importers import (  # noqa: F401
        CyclerImporter,
        MaccorImporter,
        read_maccor,
    )
except ImportError:
    pass
