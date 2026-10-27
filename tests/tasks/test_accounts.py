from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.tasks.accounts import delete_expired_activation_tokens
from tests.conftest import TEST_DATABASE_URL


# Sync tests on purpose: the task calls asyncio.run(), which can't be used
# inside the event loop that pytest-asyncio runs async tests in.


def test_task_returns_number_of_deleted_tokens() -> None:
    with patch(
        "app.tasks.accounts.crud.delete_expired_activation_tokens",
        new=AsyncMock(return_value=3),
    ) as crud_mock:
        assert delete_expired_activation_tokens() == 3

    crud_mock.assert_awaited_once()


def test_task_can_run_repeatedly_in_one_process(
    test_database: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A worker process runs the task many times; each run gets a new event loop.
    # Reusing pooled connections across loops would fail on the second run.
    test_url = TEST_DATABASE_URL.render_as_string(hide_password=False)
    monkeypatch.setattr(
        "app.tasks.accounts.settings", SimpleNamespace(database_url=test_url)
    )

    assert delete_expired_activation_tokens() == 0
    assert delete_expired_activation_tokens() == 0
