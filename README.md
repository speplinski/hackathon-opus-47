# hackathon-opus-47

Submission for the **Built with Opus 4.7** hackathon (21–26 April 2026).

**Auditable Design** — a methodology for feedback-driven design that can be
audited, challenged, and defended. The pipeline turns raw user feedback into
justified design decisions, with every Claude call logged to an append-only
replay log that reviewers can re-run byte-identical without an API key.

See [concept.md](concept.md) for the central thesis,
[ARCHITECTURE.md](ARCHITECTURE.md) for the system design, and
[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) for the day-by-day plan.

## Developer setup

Requires [uv](https://docs.astral.sh/uv/) and Python 3.11 or 3.12 (uv will
fetch a matching interpreter if one isn't on `PATH`).

```bash
uv sync --extra dev
```

Installs runtime and dev dependencies into `.venv`.

Optional, for contributors:

```bash
uv run pre-commit install
```

wires the hooks from `.pre-commit-config.yaml` — `gitleaks` (secret
scanning), `ruff` (lint + format), `pytest-collect-only` (catches broken
imports), plus the usual `pre-commit/pre-commit-hooks` file-hygiene
hooks.

## Running the test suite

No network, no API key needed:

```bash
uv run pytest -q             # full suite
uv run ruff check .          # linter
uv run mypy src/             # strict type-check
```

## Reviewer path (replay mode)

For end-to-end pipeline reproducibility, see
[ARCHITECTURE § 11.5](ARCHITECTURE.md#115-what-a-reviewer-sees). Short
version:

```bash
uv sync
python -m src.pipeline --mode replay
```

reproduces every artifact byte-identical from the committed replay log —
no Anthropic API key needed.

*(The pipeline entry-point lands during Day 3–4 of the event; for now
only the libraries and their tests are wired. Until then the `replay`
command is a forward-looking contract, documented here so that the
reproducibility story is visible from the top of the repo.)*
