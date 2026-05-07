<!--
For changes that touch wire-format behaviour: please open the upstream
PR in trendvidia/protowire and trendvidia/protowire-cpp FIRST. This port
implements the spec via the C++ codec; it shouldn't lead spec or codec
changes. See CONTRIBUTING.md.

For FFI-boundary changes (src/_protowire/module.cc, anything crossing
the nanobind shim): include a manual round-trip check against the
protowire-cpp reference fixtures.
-->

## Summary

What this PR changes, in 1–3 sentences.

## Why

Link to the issue or upstream spec/codec change that motivated this.

## Scope

- [ ] FFI boundary (`src/_protowire/`)
- [ ] Public Python API (`src/protowire/`)
- [ ] Tests / fixtures / harnesses (`tests/`, `testdata/`, `scripts/`)
- [ ] Build / packaging / CI (`pyproject.toml`, `CMakeLists.txt`, `.github/`)
- [ ] Documentation only

## Test plan

- [ ] Local build clean: `pip install -e '.[test]' && pytest`
- [ ] If FFI-boundary change: round-tripped at least one PXF + SBE fixture
      against the sibling `protowire-cpp` checkout
- [ ] If wire-impacting: matching upstream PRs linked above
- [ ] If new public symbol: exported from `protowire/__init__.py` and
      covered by a test under `tests/`
- [ ] If packaging change: cibuildwheel matrix smoke build still passes
