"""Test-to-procedure link that the generated models currently drop.

The regenerated ``TestProcedureItem`` has only ``test_procedure_subcategory``.
``test_procedure_instance`` (the procedure's OSW IRI) and
``test_procedure_instance_property`` are missing, and Pydantic drops them
without an error, so a stored test gets no ``HasProcedure`` link and the OSL
dashboard query (``[[-HasOutput.HasProcedure::...]]``) cannot find it.

Temporary local extension until the world.opensemantic.batteries schema ships
the two fields again: :class:`LinkedTestProcedureItem` adds them with the
definitions of the earlier generated model, and
:class:`ProcedureLinkedElectrochemicalTest` redeclares ``test_procedure`` with
that item class. Then drop this module.

Layer note (see CLAUDE.md): Pydantic **v1**, like the generated test models.
"""

from typing import List, Optional

from pydantic.v1 import Field

from opensemantic.batteries.v1._model import ElectrochemicalTest, TestProcedureItem

__all__ = ["LinkedTestProcedureItem", "ProcedureLinkedElectrochemicalTest"]


class LinkedTestProcedureItem(TestProcedureItem):
    """A ``TestProcedureItem`` with the procedure instance and its property."""

    test_procedure_instance: Optional[str] = Field(None, title="Instance")
    """The OSW IRI of the test procedure, e.g. ``procedure.get_iri()``."""
    test_procedure_instance_property: Optional[str] = "Property:HasProcedure"


class ProcedureLinkedElectrochemicalTest(ElectrochemicalTest):
    """An ``ElectrochemicalTest`` whose procedure items keep the link."""

    test_procedure: Optional[List[LinkedTestProcedureItem]] = Field(
        default_factory=lambda: [
            LinkedTestProcedureItem(
                test_procedure_subcategory=(
                    "Category:OSWdda41d4a4ec0421babe0295c6edcb5df"
                )
            )
        ]
    )
