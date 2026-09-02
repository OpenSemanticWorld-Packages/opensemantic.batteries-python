import json
from pathlib import Path

import yaml
from opensemantic.core.v1 import Label
from opensemantic.v1 import OswBaseModel
from osw.defaults import params as default_params
from osw.defaults import paths as default_paths
from osw.express import OswExpress
from uuid import uuid4
from uuid import UUID
from osw.model.entity import (
    CylindricalCell,
    PrismaticCell,
    ElectrochemicalCyclingDataset,
    CyclingDataRow,
)
from pydantic.v1 import Field
from typing import Any, List, Literal, Optional, Set, Union
from opensemantic.batteries.v1 import AgingTestProcedure, FormationTestProcedure, ElectrochemicalTest, \
    TestProcedureItem, ElectrochemicalTestProcedure

default_paths.cred_filepath = Path(r"../examples/accounts.pwd.yaml")
default_params.wiki_domain = "wiki-dev.open-semantic-lab.org"
wiki_domain = "wiki-dev.open-semantic-lab.org"

osw_obj = OswExpress(domain=wiki_domain, cred_filepath=default_paths.cred_filepath)


# example_characteristic = osw_obj.load_entity("Category:OSW61409bae321547e09f964f6f9cd7d779")

# example_instance = osw_obj.load_entity("Item:OSW130c65c35fb9489aa8cb2ecb860b537f")
# print(json.dumps(json.loads(example_instance.json()), indent=4)) ## data of object

aging_test_a = AgingTestProcedure(label=[Label(text="Aging Test A")])

print(f"aging_test_a with uuid : {aging_test_a.get_iri()} locally created")
test_a = ElectrochemicalTest(label=[Label(text="Test A")],
                            test_procedure = [TestProcedureItem(
                            test_procedure_subcategory= "Category:OSWdda41d4a4ec0421babe0295c6edcb5df",
                            # test_procedure_instance= aging_test_a.get_osw_id(),
                            test_procedure_instance= "Item:OSW365966aaa8d64804b5ff0351c9db5382",
                            test_procedure_instance_property = "Property:HasProcedure")]
                             )

print(f"ElectrochemicalTest with uuid : {test_a.uuid} locally created")
print(json.dumps(json.loads(test_a.json()), indent=4)) ## data of object
osw_obj.store_entity(test_a)

#
#
# cell_a = CylindricalCell(label=[Label(text="Cell A")])
# cell_b = CylindricalCell(label=[Label(text="Cell B")])
# cell_c = PrismaticCell(label=[Label(text="Cell C")])
#
# print(f"Cell with uuid : {cell_a.uuid} locally created")
# print(f"Cell with uuid : {cell_b.uuid} locally created")
# print(f"Cell with uuid : {cell_c.uuid} locally created")
# # osw_obj.store_entity(cell_a)
# # osw_obj.store_entity(cell_b)
# # osw_obj.store_entity(cell_c)
#
#
# aging_test_a = AgingTestProcedure(label=[Label(text="Aging Test A")])
# aging_test_b = AgingTestProcedure(label=[Label(text="Aging Test B")])
# formation_procedure = FormationTestProcedure(label=[Label(text="Formation Test")])
#
# print(f"aging_test_a with uuid : {aging_test_a.uuid} locally created")
# print(f"aging_test_b with uuid : {aging_test_b.uuid} locally created")
# print(f"formation_procedure with uuid : {formation_procedure.uuid} locally created")
# # osw_obj.store_entity(aging_test_a)
# # osw_obj.store_entity(aging_test_b)
# # osw_obj.store_entity(formation_procedure)
#
#
#
#
#
# # print(
# #     yaml.safe_dump(
# #         example_characteristic.to_json(exclude_defaults=True),
# #         sort_keys=False,
# #         allow_unicode=True,
# #     )
# # )
# #
#
#
# # print()
#
# # osw_obj.store_entity(ElectrochemicalCyclingDataset)
# # osw_obj.store_entity(CyclingDataRow)
