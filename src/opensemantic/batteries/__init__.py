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
    from opensemantic.batteries._cycle_summary import (  # noqa: F401
        CycleDataRow,
        CycleSummaryDataset,
        cycle_summary,
    )
    from opensemantic.batteries._cycling import (  # noqa: F401
        CyclingDatasetController,
        StepType,
        StepTypedCyclingDataRow,
    )
    from opensemantic.batteries._importers import (  # noqa: F401
        CyclerImporter,
        MaccorImporter,
        read_maccor,
    )
    from opensemantic.batteries._procedure_link import (  # noqa: F401
        LinkedTestProcedureItem,
        ProcedureLinkedElectrochemicalTest,
    )
except ImportError as _error:  # pragma: no cover - an optional dependency is missing
    import logging as _logging

    # info, not warning: the cycling extras are optional, so an install
    # without them is supported - but a missing dependency must still be
    # nameable
    _logging.getLogger(__name__).info(
        "opensemantic.batteries cycling support is unavailable: %s", _error
    )
