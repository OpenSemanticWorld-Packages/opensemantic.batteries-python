import warnings
warnings.filterwarnings("ignore")
from pydantic.warnings import PydanticDeprecatedSince20
warnings.filterwarnings("ignore", category=PydanticDeprecatedSince20)

from osw.defaults import paths as default_paths, params as default_params
from pathlib import Path

from osw.defaults import params as default_params
from osw.defaults import paths as default_paths
from osw.express import OswExpress

default_paths.cred_filepath = Path(r"../examples/accounts.pwd.yaml")
default_params.wiki_domain = "wiki-dev.open-semantic-lab.org"
wiki_domain = "wiki-dev.open-semantic-lab.org"

osw_obj = OswExpress(domain=wiki_domain, cred_filepath=default_paths.cred_filepath)



dependencies = {
"ElectrochemicalCyclingDataRow" : "Category:OSW52787b16dd264707a2d2af4a3b866936",
"ElectrochemicalCyclingDataset" : "Category:OSW5af2a0c1f6a848b591678b2473674a49",
"ElectrochemicalTest" : "Category:OSW6f39d77241e24a33ab6d036dfac03ace",
"CylindricalCell" : "Category:OSWf80456d65087488fb202f72f031d9df4",
"PrismaticCell" : "Category:OSW3d1616266eea400aa0cdae0e1d8cfead"
}


# Will run everytime the script is executed:
osw_obj.install_dependencies(dependencies,mode = "replace")

