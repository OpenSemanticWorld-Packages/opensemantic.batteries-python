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

# Cycling dataset + importers. Only the controller is re-exported here: it is
# built on the v1 models (the layer the quantitative values of its rows belong
# to), and importing its model classes as well would replace the v2 classes
# that the star-import above put in this namespace.
try:
    from opensemantic.batteries._cycling import CyclingDatasetController  # noqa: F401
    from opensemantic.batteries._importers import (  # noqa: F401
        CyclerImporter,
        MaccorImporter,
        read_maccor,
    )
except ImportError as _error:  # pragma: no cover - an optional dependency is missing
    import logging as _logging

    _logging.getLogger(__name__).warning(
        "opensemantic.batteries cycling support is unavailable: %s", _error
    )
