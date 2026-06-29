import opensemantic.batteries
import opensemantic.batteries.v1
import opensemantic.core
import opensemantic.core.v1


def test_opensemantic():

    # Create an instance of BatteryCell
    model = opensemantic.batteries.BatteryCell(
        label=[opensemantic.core.Label(text="Test Entity")],
    )

    # Check if the instance is created successfully
    assert isinstance(
        model, opensemantic.batteries.BatteryCell
    ), "Failed to create an instance of BatteryCell"

    # v1 tests

    # Create an instance of BatteryCell
    model = opensemantic.batteries.v1.BatteryCell(
        label=[opensemantic.core.v1.Label(text="Test Entity")],
    )

    # Check if the instance is created successfully
    assert isinstance(
        model, opensemantic.batteries.v1.BatteryCell
    ), "Failed to create an instance of BatteryCell"


if __name__ == "__main__":
    test_opensemantic()
    print("All tests passed!")
