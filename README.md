# picasso-registry

A standalone FastAPI + SQLAlchemy provenance & metrics database (service + thin client) for the DNA-PAINT automation stack. Append-only; everything joins on run_id. SQLite now, Postgres-ready. It **owns the schema/API contract** that PycroFlow, picasso-workflow and picasso-agent depend on.

Part of the DNA-PAINT automation stack. See `CLAUDE.md` for conventions and the
implementation playbook / plan for context.

## Quick start
```
python -m pip install -e .[dev]
pre-commit install
pytest -q
```

> New repo scaffold — Step-0 conventions applied (setuptools-scm, Black@79,
> flake8, pre-commit, CI). Tag an initial version (`git tag v0.0.1`) so builds
> resolve a version.
