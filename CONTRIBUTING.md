# Contributing

Welcome to the contributor's guide of `opensemantic.batteries`.

## Issue reports

If you find a bug or have a request, please look at the
[issue tracker] first, including closed issues, and open a new issue if you
find nothing useful. Include your operating system, your Python version and
steps to reproduce the problem. A minimal example helps most.

## Code contributions

1. Create a branch for your change. Do not work on the main branch.
2. Set up the environment, run the tests and install the pre-commit hooks as
   described in the Development section of the [README](README.md#development).
3. Add tests and documentation for a new feature.
4. Push the branch and open a pull request on the [repository].

The files `src/opensemantic/batteries/_model.py` and
`src/opensemantic/batteries/v1/_model.py` are generated. Do not edit them by
hand (see the Note section of the README).

## Documentation

The documentation is built with [Sphinx] and is written in Markdown (MyST).
Build it with `tox -e docs`.

[issue tracker]: https://github.com/OpenSemanticWorld-Packages/opensemantic.batteries-python/issues
[repository]: https://github.com/OpenSemanticWorld-Packages/opensemantic.batteries-python
[Sphinx]: https://www.sphinx-doc.org/en/master/
