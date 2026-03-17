# SPDX-FileCopyrightText: 2026 Mappalink
#
# SPDX-License-Identifier: MIT

"""Tests for the edge-executor mission support (mission_exec.py)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from inorbit_connector.connector import CommandResultCode
from inorbit_kuka_connector.src.mission_exec import (
    KukaMissionExecutor,
    KukaWorkerPool,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_kuka_api():
    api = AsyncMock()
    api.pause_mission = AsyncMock()
    api.recover_mission = AsyncMock()
    api.cancel_mission = AsyncMock()
    return api


@pytest.fixture
def result_collector():
    """Collects (code, kwargs) tuples from result_function calls."""
    results = []

    def _fn(code, **kwargs):
        results.append((code, kwargs))

    return results, _fn


@pytest.fixture
def make_executor(mock_kuka_api):
    """Create a KukaMissionExecutor with mocked internals (skip real DB/pool)."""

    def _factory(mission_code=None):
        executor = KukaMissionExecutor(
            robot_id="kuka-1",
            inorbit_api=MagicMock(),
            kuka_api=mock_kuka_api,
            get_kuka_mission_code=lambda: mission_code,
            database_file="dummy",
        )
        # Fake an initialised worker pool so handle_command doesn't bail out
        executor._initialized = True
        executor._worker_pool = AsyncMock()
        return executor

    return _factory


# ---------------------------------------------------------------------------
# KukaMissionExecutor.handle_command — routing
# ---------------------------------------------------------------------------


class TestHandleCommandRouting:
    @pytest.mark.asyncio
    async def test_routes_execute_mission_action(self, make_executor, result_collector):
        executor = make_executor()
        results, result_fn = result_collector
        options = {"result_function": result_fn}

        args = {
            "missionId": "m-1",
            "missionDefinition": "{}",
            "missionArgs": "{}",
            "options": "{}",
        }
        handled = await executor.handle_command("executeMissionAction", args, options)
        assert handled is True

    @pytest.mark.asyncio
    async def test_routes_cancel_mission_action(self, make_executor, result_collector):
        executor = make_executor()
        results, result_fn = result_collector
        options = {"result_function": result_fn}

        handled = await executor.handle_command(
            "cancelMissionAction", {"missionId": "m-1"}, options
        )
        assert handled is True

    @pytest.mark.asyncio
    async def test_routes_update_mission_action(self, make_executor, result_collector):
        executor = make_executor()
        results, result_fn = result_collector
        options = {"result_function": result_fn}

        handled = await executor.handle_command(
            "updateMissionAction", {"missionId": "m-1", "action": "pause"}, options
        )
        assert handled is True

    @pytest.mark.asyncio
    async def test_ignores_non_executor_commands(self, make_executor, result_collector):
        executor = make_executor()
        _, result_fn = result_collector
        options = {"result_function": result_fn}

        assert await executor.handle_command("move_to_node", {}, options) is False
        assert await executor.handle_command("unknown_cmd", {}, options) is False

    @pytest.mark.asyncio
    async def test_returns_false_when_not_initialized(self, mock_kuka_api, result_collector):
        executor = KukaMissionExecutor(
            robot_id="kuka-1",
            inorbit_api=MagicMock(),
            kuka_api=mock_kuka_api,
            get_kuka_mission_code=lambda: None,
            database_file="dummy",
        )
        _, result_fn = result_collector
        options = {"result_function": result_fn}

        assert (
            await executor.handle_command("executeMissionAction", {"missionId": "m-1"}, options)
            is False
        )


# ---------------------------------------------------------------------------
# KukaMissionExecutor — action handlers
# ---------------------------------------------------------------------------


class TestExecuteMissionAction:
    @pytest.mark.asyncio
    async def test_parses_json_and_submits(self, make_executor, result_collector):
        executor = make_executor()
        results, result_fn = result_collector
        options = {"result_function": result_fn}

        definition = {"steps": [{"runAction": {"actionId": "move_to_node", "arguments": {}}}]}
        args = {
            "missionId": "m-42",
            "missionDefinition": json.dumps(definition),
            "missionArgs": "{}",
            "options": "{}",
        }
        await executor.handle_command("executeMissionAction", args, options)

        executor._worker_pool.submit_work.assert_awaited_once()
        mission = executor._worker_pool.submit_work.call_args[0][0]
        assert mission.id == "m-42"
        assert results[-1][0] == CommandResultCode.SUCCESS

    @pytest.mark.asyncio
    async def test_reports_failure_on_bad_json(self, make_executor, result_collector):
        executor = make_executor()
        results, result_fn = result_collector
        options = {"result_function": result_fn}

        args = {
            "missionId": "m-bad",
            "missionDefinition": "{not-json",
        }
        await executor.handle_command("executeMissionAction", args, options)
        assert results[-1][0] == CommandResultCode.FAILURE


class TestCancelMission:
    @pytest.mark.asyncio
    async def test_calls_abort(self, make_executor, result_collector):
        executor = make_executor()
        results, result_fn = result_collector
        options = {"result_function": result_fn}

        await executor.handle_command("cancelMissionAction", {"missionId": "m-1"}, options)
        executor._worker_pool.abort_mission.assert_awaited_once_with("m-1")
        assert results[-1][0] == CommandResultCode.SUCCESS


class TestUpdateMissionAction:
    @pytest.mark.asyncio
    async def test_pause(self, make_executor, result_collector):
        executor = make_executor()
        results, result_fn = result_collector
        options = {"result_function": result_fn}

        await executor.handle_command(
            "updateMissionAction", {"missionId": "m-1", "action": "pause"}, options
        )
        executor._worker_pool.pause_mission.assert_awaited_once_with("m-1")
        assert results[-1][0] == CommandResultCode.SUCCESS

    @pytest.mark.asyncio
    async def test_resume(self, make_executor, result_collector):
        executor = make_executor()
        results, result_fn = result_collector
        options = {"result_function": result_fn}

        await executor.handle_command(
            "updateMissionAction", {"missionId": "m-1", "action": "resume"}, options
        )
        executor._worker_pool.resume_mission.assert_awaited_once_with("m-1")
        assert results[-1][0] == CommandResultCode.SUCCESS

    @pytest.mark.asyncio
    async def test_unknown_action_fails(self, make_executor, result_collector):
        executor = make_executor()
        results, result_fn = result_collector
        options = {"result_function": result_fn}

        await executor.handle_command(
            "updateMissionAction", {"missionId": "m-1", "action": "bogus"}, options
        )
        assert results[-1][0] == CommandResultCode.FAILURE


# ---------------------------------------------------------------------------
# KukaWorkerPool — KUKA API forwarding
# ---------------------------------------------------------------------------


class TestKukaWorkerPool:
    @pytest.mark.asyncio
    async def test_pause_calls_kuka_pause_by_robot_id(self, mock_kuka_api):
        pool = KukaWorkerPool(
            kuka_api=mock_kuka_api,
            get_kuka_mission_code=lambda: "MC-100",
            kuka_robot_id="1",
            api=MagicMock(),
            db=MagicMock(),
        )
        with patch("inorbit_edge_executor.worker_pool.WorkerPool.pause_mission", AsyncMock()):
            await pool.pause_mission("m-1")

        mock_kuka_api.pause_mission.assert_awaited_once_with(robot_id="1")

    @pytest.mark.asyncio
    async def test_resume_calls_kuka_recover_by_robot_id(self, mock_kuka_api):
        pool = KukaWorkerPool(
            kuka_api=mock_kuka_api,
            get_kuka_mission_code=lambda: "MC-100",
            kuka_robot_id="1",
            api=MagicMock(),
            db=MagicMock(),
        )
        with patch("inorbit_edge_executor.worker_pool.WorkerPool.resume_mission", AsyncMock()):
            await pool.resume_mission("m-1")

        mock_kuka_api.recover_mission.assert_awaited_once_with(robot_id="1")

    @pytest.mark.asyncio
    async def test_abort_calls_kuka_cancel(self, mock_kuka_api):
        pool = KukaWorkerPool(
            kuka_api=mock_kuka_api,
            get_kuka_mission_code=lambda: "MC-100",
            api=MagicMock(),
            db=MagicMock(),
        )
        with patch("inorbit_edge_executor.worker_pool.WorkerPool.abort_mission", MagicMock()):
            await pool.abort_mission("m-1")

        mock_kuka_api.cancel_mission.assert_awaited_once_with("MC-100")

    @pytest.mark.asyncio
    async def test_abort_skips_kuka_cancel_when_no_mission_code(self, mock_kuka_api):
        pool = KukaWorkerPool(
            kuka_api=mock_kuka_api,
            get_kuka_mission_code=lambda: None,
            api=MagicMock(),
            db=MagicMock(),
        )
        with patch("inorbit_edge_executor.worker_pool.WorkerPool.abort_mission", MagicMock()):
            await pool.abort_mission("m-1")

        mock_kuka_api.cancel_mission.assert_not_awaited()
