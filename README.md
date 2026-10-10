# gleanyard

Gleanyard collects structured data from recurring web sources into a schema you define.

This is an early placeholder release. The package core is under active development.

## Releasing

Publishing to PyPI is automatic through trusted publishing (no API token).

1. Bump `version` in `pyproject.toml` and merge to `main`.
2. Create a GitHub release with tag `v<version>`, e.g. `v0.0.1`.
3. The `Publish to PyPI` workflow builds the package and uploads it.
