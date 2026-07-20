# Changelog

All notable changes to **picasso-registry** are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this
project adheres to [Semantic Versioning](https://semver.org/). The version is derived from
git tags via setuptools-scm, so cutting a release means: move the `[Unreleased]` notes into a
new `[x.y.z]` section dated today, then `git tag vx.y.z`.

## [Unreleased]

### Added
- Initial repository scaffold: `pyproject.toml` (setuptools-scm, black @79, flake8),
  pre-commit config, CI workflow, `CLAUDE.md`, and a `src/picasso_registry` package
  (db, models, schemas, app, client) with a passing smoke test.
