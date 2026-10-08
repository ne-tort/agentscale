"""Unit tests — detached (autonomous) agent turns.

The core promise: a turn is owned by the API process, not the client, so a
disconnected client / page reload never stops the agent.
"""

from __future__ import annotations

import asyncio

import pytest

from prodavan.application.agent.detached_turn import DetachedTurnRegistry


@pytest.mark.asyncio
async def test_start_is_idempotent_by_turn_id() -> None:
    reg = DetachedTurnRegistry()
    started = asyncio.Event()

    async def runner(turn):  # type: ignore[no-untyped-def]
        started.set()
        await asyncio.sleep(0.05)

    t1 = await reg.start(
        turn_id="turn-1", session_id="s1", project_id="p1", start_seq=0, runner=runner
    )
    t2 = await reg.start(
        turn_id="turn-1", session_id="s1", project_id="p1", start_seq=0, runner=runner
    )
    await started.wait()

    assert t1 is t2  # duplicate send re-attaches, does not spawn a second run
    assert reg.is_working("s1") is True
    await t1.task
    # After completion the session is free again.
    assert reg.is_working("s1") is False


@pytest.mark.asyncio
async def test_run_survives_when_no_one_watches() -> None:
    """The run keeps going even if the starter never awaits it (client gone)."""
    reg = DetachedTurnRegistry()
    done = asyncio.Event()

    async def runner(turn):  # type: ignore[no-untyped-def]
        await asyncio.sleep(0.05)
        turn.assistant_text = "finished anyway"
        done.set()

    turn = await reg.start(
        turn_id="turn-2", session_id="s2", project_id="p1", start_seq=3, runner=runner
    )
    # Simulate the client disconnecting: nobody awaits the task.
    await asyncio.wait_for(done.wait(), timeout=1.0)
    assert turn.finished is True
    assert turn.assistant_text == "finished anyway"


@pytest.mark.asyncio
async def test_request_cancel_cancels_the_run() -> None:
    reg = DetachedTurnRegistry()
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def runner(turn):  # type: ignore[no-untyped-def]
        started.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    turn = await reg.start(
        turn_id="turn-3", session_id="s3", project_id="p1", start_seq=0, runner=runner
    )
    await started.wait()
    requested = reg.request_cancel("s3")

    assert requested is turn
    assert turn.cancel_requested is True
    await asyncio.wait_for(cancelled.wait(), timeout=1.0)
    # task.cancel() surfaces as CancelledError; swallow so the test is clean.
    with pytest.raises(asyncio.CancelledError):
        await turn.task
    assert reg.is_working("s3") is False


@pytest.mark.asyncio
async def test_runner_exception_marks_turn_finished() -> None:
    reg = DetachedTurnRegistry()

    async def runner(turn):  # type: ignore[no-untyped-def]
        raise RuntimeError("boom")

    turn = await reg.start(
        turn_id="turn-4", session_id="s4", project_id="p1", start_seq=0, runner=runner
    )
    await asyncio.sleep(0.05)
    assert turn.finished is True
    assert turn.error_code == "INTERNAL"
    assert reg.is_working("s4") is False


@pytest.mark.asyncio
async def test_active_for_session_ignores_finished() -> None:
    reg = DetachedTurnRegistry()

    async def runner(turn):  # type: ignore[no-untyped-def]
        return None

    turn = await reg.start(
        turn_id="turn-5", session_id="s5", project_id="p1", start_seq=0, runner=runner
    )
    await turn.task
    assert reg.active_for_session("s5") is None
    assert reg.is_working("s5") is False
