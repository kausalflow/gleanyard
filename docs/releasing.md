# Releasing

Releases publish to [PyPI](https://pypi.org/project/gleanyard/) automatically through
trusted publishing; there is no API token.

1. Change `version` in `pyproject.toml` and merge it to `main`.
2. Create a GitHub release whose tag is `v` plus that version, for example `v0.1.0`.
3. The **Publish to PyPI** workflow builds the package and uploads it. It fails if the tag
   and the version differ.

## Working on gleanyard

```sh
uv sync
uv run pytest
uv run ruff check src tests && uv run ruff format --check src tests
```

## Building these docs

The docs use [zensical](https://zensical.org/).

```sh
uv run --group docs zensical serve   # live preview
uv run --group docs zensical build   # writes site/
```

Pushes to `main` publish the site to GitHub Pages.
