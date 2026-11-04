# Changelog

## Unreleased

- Lazy dashboard: run the dataset query directly when there is no server
  session. The next-tick detour that shows "Querying ..." before the work
  tested for a Bokeh document, and a notebook kernel has one of those too -
  without a session, so the callback was never run and a selection stalled
  on "Querying datasets for N cells x M procedures..." forever. Only hit
  `show_log=True` with a cell and a procedure both selected, which is what
  the JupyterLite notebook does.
- `examples/osl/battery_dashboard_OSL.ipynb`: the OSL dashboard as a
  notebook. Runs in the wiki's JupyterLite sandbox on the browser session
  with no credentials, and on a normal kernel through pip and a
  credentials file.

- `step_type` on `CyclingDataRow` with the `StepType` enum, set by the Maccor
  importer from the `Mode` column (`R` to `REST`, `C` to `CCCV_CHG`, `D` to
  `CC_DCH`, other codes to `OTHER`).
- `CycleDataRow` and `CycleSummaryDataset` come from the schema package
  (`world.opensemantic.batteries` 1.5.0), as does the procedure link on
  `TestProcedureItem`. The local stand-ins are gone: `StepTypedCyclingDataRow`,
  `LinkedTestProcedureItem` and `ProcedureLinkedElectrochemicalTest` are
  replaced by `CyclingDataRow`, `TestProcedureItem` and `ElectrochemicalTest`.
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
