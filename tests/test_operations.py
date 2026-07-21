import asyncio

import pytest

from app.services.state import (
    IdempotencyConflict,
    InMemoryStateStore,
    RequestAlreadyProcessing,
)


@pytest.mark.asyncio
async def test_twenty_parallel_operation_reservations_allow_one_owner() -> None:
    store = InMemoryStateStore()

    async def reserve() -> str:
        try:
            result = await store.reserve_operation(
                uid="user-1",
                operation="question_generation",
                operation_id="operation-1",
                payload_hash="payload-1",
            )
            return result.status
        except RequestAlreadyProcessing:
            return "processing"

    results = await asyncio.gather(*(reserve() for _ in range(20)))
    assert results.count("new") == 1
    assert results.count("processing") == 19

    await store.complete_operation(
        uid="user-1",
        operation="question_generation",
        operation_id="operation-1",
        result={"setId": "set-1"},
        ttl_hours=24,
    )
    cached = await store.reserve_operation(
        uid="user-1",
        operation="question_generation",
        operation_id="operation-1",
        payload_hash="payload-1",
    )
    assert cached.status == "cached"
    assert cached.result == {"setId": "set-1"}


@pytest.mark.asyncio
async def test_same_operation_id_with_different_payload_conflicts() -> None:
    store = InMemoryStateStore()
    await store.reserve_operation(
        uid="user-1",
        operation="evaluation",
        operation_id="operation-1",
        payload_hash="first",
    )
    with pytest.raises(IdempotencyConflict):
        await store.reserve_operation(
            uid="user-1",
            operation="evaluation",
            operation_id="operation-1",
            payload_hash="different",
        )
