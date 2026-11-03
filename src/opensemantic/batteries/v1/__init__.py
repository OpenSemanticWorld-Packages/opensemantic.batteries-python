from importlib.metadata import PackageNotFoundError, version  # pragma: no cover

try:
    # Change here if project is renamed and does not equal the package name
    dist_name = "opensemantic.batteries"
    __version__ = version(dist_name)
except PackageNotFoundError:  # pragma: no cover
    __version__ = "unknown"
finally:
    del version, PackageNotFoundError

from opensemantic.batteries.v1._model import *  # noqa

try:
    from opensemantic.batteries.v1._controller import *  # noqa
except ImportError:
    pass

# CyclingDatasetController is built on these v1 models but lives in
# opensemantic.batteries: importing it here would re-enter this module while it
# is still initializing.
