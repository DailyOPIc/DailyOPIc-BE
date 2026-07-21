from datetime import UTC, datetime, timedelta

import pytest

from app.services.state import InMemoryStateStore, InvalidSessionTransition


@pytest.mark.asyncio
async def test_mock_session_enforces_ordered_server_stages() -> None:
    store = InMemoryStateStore()
    session = await store.create_or_get_mock_session(
        uid="user-1",
        session_id="session-1",
        session_hash="hash-1",
        date_key="20260721",
        initial_level=4,
        background={},
        survey=None,
        resets_at=datetime.now(UTC) + timedelta(hours=12),
    )
    assert session["stage"] == "awaiting_start_ad"
    with pytest.raises(InvalidSessionTransition):
        await store.transition_mock_session(
            uid="user-1",
            session_id="session-1",
            expected_stages={"answering_front"},
            stage="generating_tail",
        )

    for expected, following in (
        ("awaiting_start_ad", "generating_front"),
        ("generating_front", "answering_front"),
        ("answering_front", "generating_tail"),
        ("generating_tail", "answering_tail"),
        ("answering_tail", "evaluating"),
        ("evaluating", "completed"),
    ):
        session = await store.transition_mock_session(
            uid="user-1",
            session_id="session-1",
            expected_stages={expected},
            stage=following,
        )
    assert session["stage"] == "completed"
