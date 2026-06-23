from typer.testing import CliRunner

from lemon_ledger.cli import app

_runner = CliRunner()


def test_version_prints_version() -> None:
    result = _runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "lemon-ledger" in result.output


def test_version_output_contains_semver() -> None:
    result = _runner.invoke(app, ["version"])
    assert result.exit_code == 0
    # version string contains at least one dot (e.g. "0.1.0")
    assert "." in result.output
