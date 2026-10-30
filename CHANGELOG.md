# Changelog

## Unreleased

- `StepType` enum and the `step_type` field (`StepTypedCyclingDataRow`), set by
  the Maccor importer from the `Mode` column (`R` to `REST`, `C` to `CCCV_CHG`,
  `D` to `CC_DCH`, other codes to `OTHER`).
- Cycle series plot: charge and discharge capacity per cycle, summed over all
  charge and discharge step types, and the two coulomb efficiencies.
- Batch series plot: mean per cycle with sample standard deviation as error
  bars, and a Batches card to group the datasets.
- OSL dataset mapping (cell and procedure of each dataset) in the lazy
  dashboard, debug logging, a log console and a status line
  (`LazyBatteryDataView(show_log=True)`).
- The dashboard shows a single plot. The freeze and delete plot feature is
  removed.
- New layout defaults: the sidebar is open and the series cards are collapsed.
- The trace DataFrames are cached across plot updates (`to_df` caching).
- The examples are sorted into `examples/synthetic`, `examples/maccor`,
  `examples/osl` and `examples/dev`.
