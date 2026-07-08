import json
import yaml

from opensemantic.batteries._dataset import AirQualityDataset,BatteryCyclingDataset
from opensemantic.batteries._dataset import CyclingDataRow,AirQualityDataRow, NormalizedDataset
from opensemantic.batteries._dataset import merge_tabular_data

from opensemantic.characteristics.quantitative.v1 import (
    Count,
    ElectricCharge,
    ElectricCurrent,
    Energy,
    TabularData,
    Time,
    Voltage,
    Pressure,
    Temperature,
    Power,
)

def _sample_battery_dataset() -> BatteryCyclingDataset:
    return BatteryCyclingDataset(
        rows=[
            CyclingDataRow(
                test_time=Time(value=0.0),
                voltage=Voltage(value=3.0),
                current=ElectricCurrent(value=0.0),
            ),
            CyclingDataRow(
                test_time=Time(value=1.0),
                voltage=Voltage(value=3.1),
                current=ElectricCurrent(value=0.5),
            ),
        ]
    )

def _sample_airquality_dataset() -> AirQualityDataset:
    return AirQualityDataset(
        rows=[
            AirQualityDataRow(
                test_time=Time(value=0.0),
                temperature=Temperature(value=3.0),
                pressure=Pressure(value=0.0),
            ),
            AirQualityDataRow(
                test_time=Time(value=3.0),
                temperature=Temperature(value=3.0),
                pressure=Pressure(value=0.0),
            ),
        ]
    )


battery_dataset = _sample_battery_dataset()
air_quality_dataset = _sample_airquality_dataset()

merged_table = merge_tabular_data(battery_dataset,air_quality_dataset)

merged_df = merged_table.to_df()
merged_df["power"] = merged_df["voltage"] * merged_df["current"]

merged_table_extended = NormalizedDataset.from_df(merged_df)

payload = merged_table_extended.to_json(exclude_defaults=True)
print(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True))

# print(json.dumps(merged_table.to_json(),indent = 2))
# print(merged_table)