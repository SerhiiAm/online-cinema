from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.tasks.accounts import (
    delete_expired_activation_tokens,
    delete_expired_password_reset_tokens,
    delete_expired_refresh_tokens,
)
from tests.conftest import TEST_DATABASE_URL

# Each cleanup task and the crud function it must call.
TASKS = [
    pytest.param(
        delete_expired_activation_tokens,
        "delete_expired_activation_tokens",
        id="activation",
    ),
    pytest.param(
        delete_expired_refresh_tokens, "delete_expired_refresh_tokens", id="refresh"
    ),
    pytest.param(
        delete_expired_password_reset_tokens,
        "delete_expired_password_reset_tokens",
        id="password-reset",
    ),
]


# Sync tests on purpose: the tasks call asyncio.run(), which can't be used
# inside the event loop that pytest-asyncio runs async tests in.


@pytest.mark.parametrize(("task", "crud_name"), TASKS)
def test_task_returns_number_of_deleted_tokens(task, crud_name: str) -> None:
    with patch(
        f"app.tasks.accounts.crud.{crud_name}", new=AsyncMock(return_value=3)
    ) as crud_mock:
        assert task() == 3

    crud_mock.assert_awaited_once()


@pytest.mark.parametrize(("task", "crud_name"), TASKS)
def test_task_can_run_repeatedly_in_one_process(
    task, crud_name: str, test_database: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A worker process runs the task many times; each run gets a new event loop.
    # Reusing pooled connections across loops would fail on the second run.
    test_url = TEST_DATABASE_URL.render_as_string(hide_password=False)
    monkeypatch.setattr(
        "app.tasks.accounts.settings", SimpleNamespace(database_url=test_url)
    )

    assert task() == 0
    assert task() == 0
