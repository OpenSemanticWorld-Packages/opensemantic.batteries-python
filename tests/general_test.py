def test_import():
    import opensemantic.batteries  # noqa: F401
    import opensemantic.batteries.v1  # noqa: F401


if __name__ == "__main__":
    test_import()
    print("All tests passed!")
