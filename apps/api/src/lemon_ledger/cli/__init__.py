import importlib.metadata

import typer

app = typer.Typer(name="lemon-ledger", no_args_is_help=True)


@app.callback()
def _main() -> None:
    """Lemon Ledger – read-only crypto tax and portfolio tracker."""


@app.command()
def version() -> None:
    """Print the installed package version."""
    ver = importlib.metadata.version("lemon-ledger")
    typer.echo(f"lemon-ledger {ver}")
