from pathlib import Path

from osw.defaults import params as default_params
from osw.defaults import paths as default_paths
from osw.express import OswExpress

default_paths.cred_filepath = Path(r"../examples/accounts.pwd.yaml")
default_params.wiki_domain = "wiki-dev.open-semantic-lab.org"
wiki_domain = "wiki-dev.open-semantic-lab.org"

osw_obj = OswExpress(domain=wiki_domain, cred_filepath=default_paths.cred_filepath)




#### Subclasses of Battery Cell -> Prismatic, Zylindrical, Pouch
test_query = (f"[[SubClassOf::Category:OSW680453b7563749a0a33f6be16036c81d]]")

#### Instances of Cylindrical Cell -> Cell A, Cell B
test_query = (f"[[HasSchema::Category:OSWf80456d65087488fb202f72f031d9df4]]")

#### Subclasses of Cycling test procedure -> Aging test procedure, Formation, RPT
test_query = (f"[[SubClassOf::Category:OSWd4bff0a542404ed5b70fcd79e4815b2b]]")

#### Instances of Aging test procedure -> AgingTestA, AgingTestB
test_query = (f"[[HasSchema::Category:OSW1b877eab05e442999835f2f595d1d1e7]]")
#
# ### Instances of
# test_query = (f"[[HasSchema::Category:OSW5af2a0c1f6a848b591678b2473674a49]]")

### Instances of Electrochemical Test
test_query = (f"[[HasSchema::Category:OSW6f39d77241e24a33ab6d036dfac03ace]]")
### Instances of Electrochemical Test with DUT Cell B (### Instances of Item:OSW4a20efb16be64868ab9d16a97838434a) -> CellBFormation, CellBAgingA
test_query = (f"[[HasSchema::Category:OSW6f39d77241e24a33ab6d036dfac03ace]][[HasDut::Item:OSW4a20efb16be64868ab9d16a97838434a]]")

### Instances of Electrochemical Test with DUT Cell B (### Instances of Item:OSW4a20efb16be64868ab9d16a97838434a)
# with Procedure Aging Test A (OSW365966aaa8d64804b5ff0351c9db5382) -> CellBAgingA
test_query = (f"[[HasSchema::Category:OSW6f39d77241e24a33ab6d036dfac03ace]][[HasDut::Item:OSW4a20efb16be64868ab9d16a97838434a]]"
              f"[[HasProcedure::Item:OSW365966aaa8d64804b5ff0351c9db5382]]")



### OUTPUT of this:
# Instances of Electrochemical Test with DUT Cell B (### Instances of Item:OSW4a20efb16be64868ab9d16a97838434a)
# with Procedure Aging Test A (OSW365966aaa8d64804b5ff0351c9db5382) -> CellBAgingA
test_query = (f"[[-HasOutput.HasSchema::Category:OSW6f39d77241e24a33ab6d036dfac03ace]][[-HasOutput.HasDut::Item:OSW4a20efb16be64868ab9d16a97838434a]]"
              f"[[-HasOutput.HasProcedure::Item:OSW365966aaa8d64804b5ff0351c9db5382]]")




results = osw_obj.site.semantic_search(
    osw_obj.site.SearchParam(query=(test_query), debug = False)
)


print(results)

# for r in results:
#     # entity = osw_obj.load_entity(r)
#     # print(f"'{entity.name}' : '{r}',")
#     # print(r)
#     try:
#         name = osw_obj.site.get_page_content([r]).contents[r]["jsondata"]["name"]
#         print(f"'{name}' : '{r}',")
#         jsondata = osw_obj.site.get_page_content([r]).contents[r]["jsondata"]
#         print(jsondata)
#     except:
#         pass


for r in results:
    # entity = osw_obj.load_entity(r)
    # print(f"'{entity.name}' : '{r}',")
    # print(r)
    try:
        name = osw_obj.site.get_page_content([r]).contents[r]["jsondata"]["name"]
        print(f"'{name}' : '{r}',")
        entity = osw_obj.load_entity(r)
        data = entity.data
        print(data)

    except:
        pass


