"""CLI unit tests via typer's CliRunner.

DB-touching commands are tested by monkeypatching _get_sessionmaker so no
real database is needed for these cases.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from lemon_ledger.cli import app

runner = CliRunner()


# ── wallet-add address validation ─────────────────────────────────────────────


def test_wallet_add_invalid_address_exits_1() -> None:
    result = runner.invoke(
        app,
        [
            "wallet",
            "add",
            "--address",
            "not-an-address",
            "--chain",
            "lemonchain",
            "--user-id",
            str(uuid.uuid4()),
            "--entity-id",
            str(uuid.uuid4()),
        ],
    )
    assert result.exit_code == 1
    assert "Invalid address" in result.output


def test_wallet_add_valid_address_pattern_passes_validation() -> None:
    valid_addr = "0x" + "a" * 40
    mock_session = MagicMock()
    mock_session.__enter__ = lambda s: s
    mock_session.__exit__ = MagicMock(return_value=False)
    mock_maker = MagicMock()
    mock_maker.return_value = mock_session

    with patch("lemon_ledger.cli._get_sessionmaker", return_value=mock_maker):
        result = runner.invoke(
            app,
            [
                "wallet",
                "add",
                "--address",
                valid_addr,
                "--chain",
                "lemonchain",
                "--user-id",
                str(uuid.uuid4()),
                "--entity-id",
                str(uuid.uuid4()),
            ],
        )
    # Either succeeds or fails on DB mock, but should NOT fail on address validation
    assert "Invalid address" not in result.output


# ── sync command: wallet not found ────────────────────────────────────────────


def test_sync_wallet_not_found_exits_1() -> None:
    mock_session = MagicMock()
    mock_session.scalars.return_value.first.return_value = None
    mock_ctx_manager = MagicMock()
    mock_ctx_manager.__enter__ = MagicMock(return_value=mock_session)
    mock_ctx_manager.__exit__ = MagicMock(return_value=False)

    mock_maker = MagicMock()

    with (
        patch("lemon_ledger.cli._get_sessionmaker", return_value=mock_maker),
        patch("lemon_ledger.cli.worker_session", return_value=mock_ctx_manager),
    ):
        result = runner.invoke(
            app,
            [
                "sync",
                "--wallet",
                "0x" + "b" * 40,
                "--chain",
                "lemonchain",
                "--local",
            ],
        )
    assert result.exit_code == 1
    assert "not found" in result.output


def test_sync_wallet_found_triggers_task() -> None:
    w = MagicMock()
    w.id = uuid.uuid4()

    mock_session = MagicMock()
    mock_session.scalars.return_value.first.return_value = w
    mock_ctx_manager = MagicMock()
    mock_ctx_manager.__enter__ = MagicMock(return_value=mock_session)
    mock_ctx_manager.__exit__ = MagicMock(return_value=False)

    mock_task_result = MagicMock()
    mock_task_result.get.return_value = {
        "wallet_id": str(w.id),
        "transactions": 0,
        "token_transfers": 0,
        "internal_txs": 0,
    }

    with (
        patch("lemon_ledger.cli._get_sessionmaker"),
        patch("lemon_ledger.cli.worker_session", return_value=mock_ctx_manager),
        patch("lemon_ledger.tasks.sync.sync_wallet_task") as mock_task,
    ):
        mock_task.apply.return_value = mock_task_result
        result = runner.invoke(
            app,
            [
                "sync",
                "--wallet",
                "0x" + "b" * 40,
                "--chain",
                "lemonchain",
                "--local",
            ],
        )
    assert result.exit_code == 0
    assert str(w.id) in result.output
