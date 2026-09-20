"""Unit tests for the client registry driving server auto-shutdown."""
# mypy: ignore-errors

from __future__ import annotations

import asyncio

import pytest

from langharness_api.common.client_registry import ClientRegistry


@pytest.mark.asyncio
async def test_attach_and_detach_count_clients() -> None:
    registry = ClientRegistry()
    client = object()
    assert registry.active_count() == 0
    registry.attach(client)
    assert registry.active_count() == 1
    registry.attach(client)
    assert registry.active_count() == 1
    registry.detach(client)
    assert registry.active_count() == 0


@pytest.mark.asyncio
async def test_last_detach_schedules_shutdown_after_grace() -> None:
    shutdowns: list[int] = []
    registry = ClientRegistry(grace=0.01)
    registry.set_enabled(True)
    registry.set_shutdown_callback(lambda: shutdowns.append(1))
    registry.mark_ready()

    first, second = object(), object()
    registry.attach(first)
    registry.attach(second)
    registry.detach(first)
    await asyncio.sleep(0.05)
    assert shutdowns == []
    registry.detach(second)
    await asyncio.sleep(0.05)
    assert shutdowns == [1]


@pytest.mark.asyncio
async def test_attach_during_grace_cancels_shutdown() -> None:
    shutdowns: list[int] = []
    registry = ClientRegistry(grace=0.05)
    registry.set_enabled(True)
    registry.set_shutdown_callback(lambda: shutdowns.append(1))
    registry.mark_ready()

    first, second = object(), object()
    registry.attach(first)
    registry.detach(first)
    registry.attach(second)  # immediately re-attach, within grace
    await asyncio.sleep(0.1)
    assert shutdowns == []


@pytest.mark.asyncio
async def test_not_ready_never_schedules_until_marked_ready() -> None:
    shutdowns: list[int] = []
    registry = ClientRegistry(grace=0.01)
    registry.set_enabled(True)
    registry.set_shutdown_callback(lambda: shutdowns.append(1))

    client = object()
    registry.attach(client)
    registry.detach(client)
    await asyncio.sleep(0.05)
    assert shutdowns == []
    registry.mark_ready()  # empty at ready -> schedules once
    await asyncio.sleep(0.05)
    assert shutdowns == [1]


@pytest.mark.asyncio
async def test_disabled_registry_never_shuts_down() -> None:
    shutdowns: list[int] = []
    registry = ClientRegistry(grace=0.01)
    registry.set_enabled(False)
    registry.set_shutdown_callback(lambda: shutdowns.append(1))
    registry.mark_ready()

    client = object()
    registry.attach(client)
    registry.detach(client)
    await asyncio.sleep(0.05)
    assert shutdowns == []


@pytest.mark.asyncio
async def test_missing_shutdown_callback_is_safe() -> None:
    registry = ClientRegistry(grace=0.01)
    registry.set_enabled(True)
    registry.mark_ready()

    client = object()
    registry.attach(client)
    registry.detach(client)
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_mark_ready_with_clients_does_not_schedule() -> None:
    shutdowns: list[int] = []
    registry = ClientRegistry(grace=0.01)
    registry.set_enabled(True)
    registry.set_shutdown_callback(lambda: shutdowns.append(1))

    client = object()
    registry.attach(client)
    registry.mark_ready()
    await asyncio.sleep(0.05)
    assert shutdowns == []
    registry.detach(client)
    await asyncio.sleep(0.05)
    assert shutdowns == [1]


@pytest.mark.asyncio
async def test_second_mark_ready_reuses_pending_timer() -> None:
    shutdowns: list[int] = []
    registry = ClientRegistry(grace=0.01)
    registry.set_enabled(True)
    registry.set_shutdown_callback(lambda: shutdowns.append(1))

    registry.mark_ready()
    registry.mark_ready()  # timer already pending: no second timer
    await asyncio.sleep(0.05)
    assert shutdowns == [1]


@pytest.mark.asyncio
async def test_timer_fire_after_disable_is_a_noop() -> None:
    shutdowns: list[int] = []
    registry = ClientRegistry(grace=0.02)
    registry.set_enabled(True)
    registry.set_shutdown_callback(lambda: shutdowns.append(1))
    registry.mark_ready()  # schedules the timer
    registry.set_enabled(False)  # before the timer fires
    await asyncio.sleep(0.06)
    assert shutdowns == []
