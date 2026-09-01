"""Minimal cycling-data example following schema_addon conventions.

Cell and procedure classes are imported from ``opensemantic.batteries.v1`` where
they already exist (``BatteryCell``, ``ElectrochemicalTestProcedure`` and its
``AgingTestProcedure`` / ``FormationTestProcedure`` subclasses). Only the
example-specific test run, dataset and cell form-factor classes are defined here.
"""

### Pointers to
# ---------------------------------------------------------------------------
# Cells
# ---------------------------------------------------------------------------

cell_a = "Item:OSW7bae5d74c11842fc8fdc5f12d264a5f1"
cell_b = "Item:OSW4a20efb16be64868ab9d16a97838434a"
cell_c = "Item:OSW35ff60500092495ba72d0624f830129b"

# ---------------------------------------------------------------------------
# Procedures (protocol instances)
# ---------------------------------------------------------------------------


aging_test_a = "Item:OSW365966aaa8d64804b5ff0351c9db5382"
aging_test_b = "Item:OSW606b66a2c1a94f8c86c3821807cf9bff"
formation_procedure = "Item:OSWecce41274e5b403a9de4179b04b49a1e"

# ---------------------------------------------------------------------------
# Test runs
# ---------------------------------------------------------------------------


test_cell_a_aging_a = "Item:OSW3b8adbb8c9ce4ac7ae89d30de43a1d05"

test_cell_a_formation ="Item:OSW938e6a74e85a47b0b5a2a355e2ce6b94"

# Cell B: AgingTestA + AgingTestB + Formation
test_cell_b_aging_a = "Item:OSW40d0053068a8495fbbe8526b20f4d7e9"

test_cell_b_aging_b = "Item:OSW30313ec0213a42eeb3033e24583cb0d4"

test_cell_b_formation = "Item:OSW1abc2aa549cf496c9a1c6bd3f5728717"
# Cell C: Formation only
test_cell_c_formation = "Item:OSW2beded327c644d2e9cb2352a3f9eecac"



