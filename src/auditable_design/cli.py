"""Typer CLI entry point for Auditable Design.

Registered in `pyproject.toml` as the `auditable` script. Commands are
thin wrappers around the layer modules — the real logic lives in
`src/auditable_design/layers/*.py` (see ARCHITECTURE.md §5.1).

Day 1 status: scaffold only. Subcommands raise NotImplementedError
until the corresponding layer module lands.
"""

from __future__ import annotations

import typer

app = typer.Typer(
    name="auditable",
    help="Auditable Design pipeline — run, replay, and inspect layers.",
    no_args_is_help=True,
    add_completion=False,
)


@app.command()
def version() -> None:
    """Print the package version."""
    from auditable_design import __version__

    typer.echo(__version__)


@app.command()
def run(
    run_id: str = typer.Option(..., "--run-id", help="Run identifier, e.g. '2026-04-23_pilot'."),
    layers: str = typer.Option("1-10", "--layers", help="Layer range to execute, e.g. '1-4' or 'all'."),
    mode: str = typer.Option("live", "--mode", help="Claude client mode: 'live' or 'replay'."),
) -> None:
    """Run the pipeline (scaffold — not yet implemented)."""
    del run_id, layers, mode
    raise NotImplementedError("Pipeline orchestration lands with layer 1 (task #19).")


if __name__ == "__main__":
    app()
