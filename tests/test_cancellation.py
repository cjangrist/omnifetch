"""Behavior of ``cancel_task``: never cancel twice, repeat an absorbed cancel.

The end-to-end and canary cases use a real local TCP listener and the real
``anyio.connect_tcp``. A plain ``Task.cancel()`` landing as a connection attempt
succeeds is lost to agronholm/anyio#1214; the canary asserts that this is still
true of the locked AnyIO, and fails once a release with the fix is locked --
at which point the recheck in ``cancellation.py`` and the canary are deleted.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable

import anyio
import pytest

from omnifetch.fetch.shared.cancellation import (
    ABSORBED_CANCELLATION_RECHECK_SECONDS,
    cancel_task,
)

_OFFSETS = range(100)


async def _wait_until(predicate: Callable[[], bool]) -> None:
    async with asyncio.timeout(1):
        while not predicate():
            await asyncio.sleep(0)


async def test_task_already_unwinding_is_not_cancelled_again() -> None:
    unwinding = asyncio.Event()
    release = asyncio.Event()

    async def unwind_slowly() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            unwinding.set()
            await release.wait()
            raise

    task = asyncio.create_task(unwind_slowly())
    await asyncio.sleep(0)
    task.cancel()
    await _wait_until(unwinding.is_set)

    cancel_task(task)
    await asyncio.sleep(ABSORBED_CANCELLATION_RECHECK_SECONDS * 2)

    assert task.cancelling() == 1
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_absorbed_cancellation_is_repeated_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    absorbed = asyncio.Event()

    async def absorb_first_cancellation() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            current = asyncio.current_task()
            assert current is not None
            current.uncancel()
            absorbed.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(absorb_first_cancellation(), name="absorber")
    await asyncio.sleep(0)
    with caplog.at_level(
        logging.WARNING, logger="omnifetch.fetch.cancellation"
    ):
        cancel_task(task)
        await _wait_until(task.done)

    assert absorbed.is_set()
    assert task.cancelled()
    assert "absorber" in caplog.text
    assert "anyio#1214" in caplog.text


async def test_finished_task_is_left_alone() -> None:
    async def finish() -> str:
        return "done"

    finished = asyncio.create_task(finish())
    await finished
    cancel_task(finished)

    quick = asyncio.create_task(finish())
    cancel_task(quick)
    await asyncio.sleep(ABSORBED_CANCELLATION_RECHECK_SECONDS * 2)

    assert finished.result() == "done"
    assert quick.cancelled()


async def _accept(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> None:
    await reader.read()
    writer.close()


async def _connect_then_wait(port: int) -> None:
    stream = await anyio.connect_tcp("127.0.0.1", port)
    try:
        await asyncio.Event().wait()
    finally:
        await stream.aclose()


async def _cancelled_at_offset(
    port: int, offset: int, cancel: Callable[[asyncio.Task[None]], object]
) -> bool:
    task = asyncio.create_task(_connect_then_wait(port))
    for _ in range(offset):
        await asyncio.sleep(0)
    cancel(task)
    await asyncio.wait(
        {task}, timeout=ABSORBED_CANCELLATION_RECHECK_SECONDS * 3
    )
    cancelled = task.cancelled()
    if not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    return cancelled


async def _sweep_offsets(
    cancel: Callable[[asyncio.Task[None]], object],
    *,
    stop_at_first_lost: bool,
) -> list[bool]:
    """Cancel one connecting task per offset; report which ended cancelled.

    Each trial ends as soon as its task finishes, so a wide sweep is cheap and
    still crosses the instant ``connect_tcp`` succeeds on a slow machine.

    The listener is closed without waiting for its connections: AnyIO leaves
    a socket that connected just before a cancellation to the garbage
    collector, so the server side of it can stay open after the test ends.
    """
    listener = await asyncio.start_server(_accept, "127.0.0.1", 0)
    port = listener.sockets[0].getsockname()[1]
    outcomes: list[bool] = []
    with contextlib.closing(listener):
        for offset in _OFFSETS:
            outcomes.append(await _cancelled_at_offset(port, offset, cancel))
            if stop_at_first_lost and not outcomes[-1]:
                break
    return outcomes


async def test_cancel_task_survives_anyio_connect_tcp(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(
        logging.WARNING, logger="omnifetch.fetch.cancellation"
    ):
        outcomes = await _sweep_offsets(cancel_task, stop_at_first_lost=False)

    assert all(outcomes)
    assert "anyio#1214" in caplog.text


async def test_anyio_still_absorbs_concurrent_cancellation() -> None:
    """Canary for agronholm/anyio#1214.

    Fails once the locked AnyIO includes the fix (PR #1330): delete
    ``_cancel_again_if_absorbed`` and its scheduling in ``cancellation.py``,
    then this test.
    """
    outcomes = await _sweep_offsets(
        asyncio.Task.cancel, stop_at_first_lost=True
    )

    assert not all(outcomes)
