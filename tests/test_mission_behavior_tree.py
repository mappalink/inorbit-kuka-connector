# SPDX-FileCopyrightText: 2026 Mappalink
#
# SPDX-License-Identifier: MIT

"""Tests for the KUKA-specific behavior tree nodes and step builder."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from inorbit_edge_executor.datatypes import (
    MissionRuntimeOptions,
    MissionRuntimeSharedMemory,
    MissionStepPoseWaypoint,
    MissionStepRunAction,
    MissionStepWait,
    Pose,
)

from inorbit_kuka_connector.src.mission.behavior_tree import (
    KukaActionNode,
    KukaBehaviorTreeBuilderContext,
    KukaMissionAbortedNode,
    KukaMoveToNodeNode,
    KukaNodeFromStepBuilder,
    SharedMemoryKeys,
    WaitForKukaCompletionNode,
    _find_nearest_node,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SAMPLE_NODES = [
    ("NODE-001", 1.0, 2.0),
    ("NODE-002", 5.0, 5.0),
    ("NODE-003", 10.0, 0.0),
]


def _make_context(
    nodes=None,
    node_margin_m=5.0,
    robot_query_responses=None,
    kuka_robot_id="100",
    robot_model="KMP 600P-EU-DIC diffDrive",
):
    """Build a KukaBehaviorTreeBuilderContext with mocked KUKA API.

    Pass nodes=[] explicitly to test with no nodes (default is SAMPLE_NODES).
    """
    kuka_api = AsyncMock()
    kuka_api.submit_move_mission = AsyncMock(return_value=({"success": True}, "CONN-test1234"))
    kuka_api.robot_lift = AsyncMock(return_value={"success": True})
    kuka_api.robot_drop = AsyncMock(return_value={"success": True})
    kuka_api.robot_move_carry = AsyncMock(return_value={"success": True})
    kuka_api.charge_robot = AsyncMock(return_value={"success": True})
    kuka_api.cancel_mission = AsyncMock(return_value={"success": True})

    if robot_query_responses:
        kuka_api.robot_query = AsyncMock(side_effect=robot_query_responses)
    else:
        # Default: immediately idle
        kuka_api.robot_query = AsyncMock(return_value={"success": True, "data": [{"status": 3}]})

    ctx = KukaBehaviorTreeBuilderContext(
        kuka_api=kuka_api,
        kuka_robot_id=kuka_robot_id,
        robot_model=robot_model,
        nodes=SAMPLE_NODES if nodes is None else nodes,
        node_margin_m=node_margin_m,
    )
    ctx.mission = MagicMock()
    ctx.mission.id = "test-mission"
    ctx.mission.robot_id = "kuka-1"
    ctx.error_context = {}
    ctx.options = MissionRuntimeOptions()
    ctx.shared_memory = MissionRuntimeSharedMemory()
    ctx.shared_memory.add(SharedMemoryKeys.KUKA_ACTIVE_MISSION_CODE, None)
    ctx.shared_memory.add(SharedMemoryKeys.KUKA_ERROR_MESSAGE, None)
    ctx.mt = AsyncMock()
    ctx.robot_api = MagicMock()
    ctx.robot_api_factory = MagicMock()
    return ctx


# ---------------------------------------------------------------------------
# _find_nearest_node
# ---------------------------------------------------------------------------


class TestFindNearestNode:
    def test_finds_closest(self):
        code, dist = _find_nearest_node(SAMPLE_NODES, 1.1, 2.1)
        assert code == "NODE-001"
        assert dist < 0.2

    def test_empty_nodes(self):
        code, dist = _find_nearest_node([], 1.0, 2.0)
        assert code is None
        assert dist == 0.0


# ---------------------------------------------------------------------------
# KukaMoveToNodeNode
# ---------------------------------------------------------------------------


class TestKukaMoveToNodeNode:
    @pytest.mark.asyncio
    async def test_calls_submit_move_mission(self):
        ctx = _make_context()
        node = KukaMoveToNodeNode(ctx, node_code="NODE-001", label="test")
        ctx.shared_memory.freeze()
        await node._execute()

        ctx.kuka_api.submit_move_mission.assert_awaited_once_with(
            "100", "NODE-001", "KMP 600P-EU-DIC diffDrive"
        )
        assert ctx.shared_memory.get(SharedMemoryKeys.KUKA_ACTIVE_MISSION_CODE) == "CONN-test1234"

    @pytest.mark.asyncio
    async def test_raises_on_failure(self):
        ctx = _make_context()
        ctx.kuka_api.submit_move_mission = AsyncMock(return_value=({"success": False}, "CONN-fail"))
        node = KukaMoveToNodeNode(ctx, node_code="NODE-001", label="test")
        ctx.shared_memory.freeze()

        with pytest.raises(RuntimeError, match="submitMission failed"):
            await node._execute()

    @pytest.mark.asyncio
    async def test_raises_on_exception(self):
        ctx = _make_context()
        ctx.kuka_api.submit_move_mission = AsyncMock(side_effect=ConnectionError("offline"))
        node = KukaMoveToNodeNode(ctx, node_code="NODE-001", label="test")
        ctx.shared_memory.freeze()

        with pytest.raises(RuntimeError, match="offline"):
            await node._execute()

    def test_dump_object(self):
        ctx = _make_context()
        node = KukaMoveToNodeNode(ctx, node_code="NODE-001", label="test")
        obj = node.dump_object()
        assert obj["node_code"] == "NODE-001"


# ---------------------------------------------------------------------------
# WaitForKukaCompletionNode
# ---------------------------------------------------------------------------


class TestWaitForKukaCompletionNode:
    @pytest.mark.asyncio
    async def test_returns_when_idle(self):
        ctx = _make_context(
            robot_query_responses=[
                {"success": True, "data": [{"status": 4}]},  # executing
                {"success": True, "data": [{"status": 3}]},  # idle
            ]
        )
        node = WaitForKukaCompletionNode(ctx, label="test")
        ctx.shared_memory.freeze()
        await node._execute()

        assert ctx.kuka_api.robot_query.await_count == 2

    @pytest.mark.asyncio
    async def test_returns_when_charging(self):
        ctx = _make_context(
            robot_query_responses=[
                {"success": True, "data": [{"status": 4}]},  # executing
                {"success": True, "data": [{"status": 5}]},  # charging
            ]
        )
        node = WaitForKukaCompletionNode(ctx, label="test")
        ctx.shared_memory.freeze()
        await node._execute()

    @pytest.mark.asyncio
    async def test_raises_on_abnormal(self):
        ctx = _make_context(
            robot_query_responses=[
                {
                    "success": True,
                    "data": [{"status": 7, "errorMessage": "obstacle"}],
                },
            ]
        )
        node = WaitForKukaCompletionNode(ctx, label="test")
        ctx.shared_memory.freeze()

        with pytest.raises(RuntimeError, match="Abnormal"):
            await node._execute()

    @pytest.mark.asyncio
    async def test_timeout(self):
        ctx = _make_context(
            robot_query_responses=[
                {"success": True, "data": [{"status": 4}]},
                {"success": True, "data": [{"status": 4}]},
                {"success": True, "data": [{"status": 4}]},
            ]
        )
        node = WaitForKukaCompletionNode(ctx, timeout_secs=1.5, label="test")
        ctx.shared_memory.freeze()

        with pytest.raises(RuntimeError, match="timed out"):
            await node._execute()

    @pytest.mark.asyncio
    async def test_survives_poll_error(self):
        ctx = _make_context(
            robot_query_responses=[
                {"success": True, "data": [{"status": 4}]},  # executing
                ConnectionError("network blip"),
                {"success": True, "data": [{"status": 3}]},  # idle
            ]
        )
        node = WaitForKukaCompletionNode(ctx, label="test")
        ctx.shared_memory.freeze()
        await node._execute()

        assert ctx.kuka_api.robot_query.await_count == 3


# ---------------------------------------------------------------------------
# KukaActionNode
# ---------------------------------------------------------------------------


class TestKukaActionNode:
    @pytest.mark.asyncio
    async def test_lift_blind(self):
        """Simple lift — just raise mechanism, no args."""
        ctx = _make_context()
        node = KukaActionNode(ctx, action_name="lift", action_args={}, label="test")
        ctx.shared_memory.freeze()
        await node._execute()

        ctx.kuka_api.robot_lift.assert_awaited_once_with("100")

    @pytest.mark.asyncio
    async def test_container_lift(self):
        """Container lift — explicit container code."""
        ctx = _make_context()
        node = KukaActionNode(
            ctx,
            action_name="container_lift",
            action_args={"container_code": "C-100"},
            label="test",
        )
        ctx.shared_memory.freeze()
        await node._execute()

        ctx.kuka_api.robot_lift.assert_awaited_once_with("100", "C-100")

    @pytest.mark.asyncio
    async def test_drop_in_place(self):
        """Simple drop — lower mechanism in place, no args."""
        ctx = _make_context()
        node = KukaActionNode(ctx, action_name="drop", action_args={}, label="test")
        ctx.shared_memory.freeze()
        await node._execute()

        ctx.kuka_api.robot_drop.assert_awaited_once_with("100")

    @pytest.mark.asyncio
    async def test_container_drop(self):
        """Container drop — explicit node code."""
        ctx = _make_context()
        node = KukaActionNode(
            ctx,
            action_name="container_drop",
            action_args={"node_code": "NODE-001"},
            label="test",
        )
        ctx.shared_memory.freeze()
        await node._execute()

        ctx.kuka_api.robot_drop.assert_awaited_once_with("100", "NODE-001")

    @pytest.mark.asyncio
    async def test_move_carry(self):
        ctx = _make_context()
        node = KukaActionNode(
            ctx,
            action_name="move_carry",
            action_args={"container_code": "C-100", "node_code": "NODE-002"},
            label="test",
        )
        ctx.shared_memory.freeze()
        await node._execute()

        ctx.kuka_api.robot_move_carry.assert_awaited_once_with("100", "C-100", "NODE-002")

    @pytest.mark.asyncio
    async def test_charge(self):
        ctx = _make_context()
        node = KukaActionNode(
            ctx,
            action_name="charge",
            action_args={},
            label="test",
        )
        ctx.shared_memory.freeze()
        await node._execute()

        ctx.kuka_api.charge_robot.assert_awaited_once_with("100")

    @pytest.mark.asyncio
    async def test_missing_arg_raises(self):
        ctx = _make_context()
        node = KukaActionNode(
            ctx,
            action_name="container_lift",
            action_args={},  # missing container_code
            label="test",
        )
        ctx.shared_memory.freeze()

        with pytest.raises(RuntimeError, match="missing argument"):
            await node._execute()

    @pytest.mark.asyncio
    async def test_unknown_action_raises(self):
        ctx = _make_context()
        node = KukaActionNode(ctx, action_name="explode", action_args={}, label="test")
        ctx.shared_memory.freeze()

        with pytest.raises(RuntimeError, match="Unknown KUKA action"):
            await node._execute()

    @pytest.mark.asyncio
    async def test_api_failure_raises(self):
        ctx = _make_context()
        ctx.kuka_api.robot_lift = AsyncMock(return_value={"success": False})
        node = KukaActionNode(
            ctx,
            action_name="container_lift",
            action_args={"container_code": "C-100"},
            label="test",
        )
        ctx.shared_memory.freeze()

        with pytest.raises(RuntimeError, match="container_lift failed"):
            await node._execute()

    def test_dump_object(self):
        ctx = _make_context()
        node = KukaActionNode(
            ctx,
            action_name="container_lift",
            action_args={"container_code": "C-100"},
            label="test",
        )
        obj = node.dump_object()
        assert obj["action_name"] == "container_lift"
        assert obj["action_args"] == {"container_code": "C-100"}


# ---------------------------------------------------------------------------
# KukaMissionAbortedNode
# ---------------------------------------------------------------------------


class TestKukaMissionAbortedNode:
    @pytest.mark.asyncio
    async def test_cancels_from_shared_memory(self):
        ctx = _make_context()
        ctx._get_kuka_mission_code = lambda: None
        node = KukaMissionAbortedNode(ctx, label="test")
        ctx.shared_memory.freeze()
        ctx.shared_memory.set(SharedMemoryKeys.KUKA_ACTIVE_MISSION_CODE, "MC-SM-1")

        await node._execute()

        ctx.kuka_api.cancel_mission.assert_awaited_once_with("MC-SM-1")

    @pytest.mark.asyncio
    async def test_cancels_from_getter_fallback(self):
        ctx = _make_context()
        ctx._get_kuka_mission_code = lambda: "MC-42"
        node = KukaMissionAbortedNode(ctx, label="test")
        ctx.shared_memory.freeze()

        await node._execute()

        ctx.kuka_api.cancel_mission.assert_awaited_once_with("MC-42")

    @pytest.mark.asyncio
    async def test_skips_cancel_when_no_mission(self):
        ctx = _make_context()
        ctx._get_kuka_mission_code = lambda: None
        node = KukaMissionAbortedNode(ctx, label="test")
        ctx.shared_memory.freeze()

        await node._execute()

        ctx.kuka_api.cancel_mission.assert_not_awaited()


# ---------------------------------------------------------------------------
# KukaNodeFromStepBuilder
# ---------------------------------------------------------------------------


class TestKukaNodeFromStepBuilder:
    def test_visit_pose_waypoint_builds_sequence(self):
        ctx = _make_context(nodes=SAMPLE_NODES, node_margin_m=5.0)
        builder = KukaNodeFromStepBuilder(ctx)

        step = MissionStepPoseWaypoint(
            waypoint=Pose(x=1.0, y=2.0, theta=0.0, frameId="map"),
            label="Go to NODE-001",
        )
        tree = step.accept(builder)

        assert tree is not None
        assert "NODE-001" in tree.label

    def test_visit_pose_waypoint_rejects_far_node(self):
        ctx = _make_context(nodes=SAMPLE_NODES, node_margin_m=0.01)
        builder = KukaNodeFromStepBuilder(ctx)

        step = MissionStepPoseWaypoint(
            waypoint=Pose(x=100.0, y=100.0, theta=0.0, frameId="map"),
            label="Far away",
        )
        with pytest.raises(RuntimeError, match="too far"):
            step.accept(builder)

    def test_visit_pose_waypoint_rejects_no_nodes(self):
        ctx = _make_context(nodes=[])
        builder = KukaNodeFromStepBuilder(ctx)

        step = MissionStepPoseWaypoint(
            waypoint=Pose(x=1.0, y=2.0, theta=0.0, frameId="map"),
        )
        with pytest.raises(RuntimeError, match="no KUKA nodes loaded"):
            step.accept(builder)

    def test_visit_run_action_navigate_to(self):
        ctx = _make_context(nodes=SAMPLE_NODES, node_margin_m=5.0)
        builder = KukaNodeFromStepBuilder(ctx)

        step = MissionStepRunAction(
            runAction={
                "actionId": "NavigateTo-000000",
                "arguments": {"pose": {"x": 1.0, "y": 2.0, "theta": 0.0}},
            },
        )
        tree = step.accept(builder)
        assert tree is not None

    def test_visit_run_action_lift(self):
        """Simple lift — no args required."""
        ctx = _make_context()
        builder = KukaNodeFromStepBuilder(ctx)

        step = MissionStepRunAction(
            runAction={"actionId": "lift", "arguments": {}},
        )
        tree = step.accept(builder)
        assert tree is not None

    def test_visit_run_action_container_lift(self):
        """Container lift — requires container_code."""
        ctx = _make_context()
        builder = KukaNodeFromStepBuilder(ctx)

        step = MissionStepRunAction(
            runAction={
                "actionId": "container_lift",
                "arguments": {"container_code": "C-100"},
            },
        )
        tree = step.accept(builder)
        assert tree is not None

    def test_visit_run_action_unknown_falls_back(self):
        ctx = _make_context()
        builder = KukaNodeFromStepBuilder(ctx)

        step = MissionStepRunAction(
            runAction={
                "actionId": "some-custom-action-000",
                "arguments": {},
            },
        )
        # Should not raise — falls back to default (cloud) handler
        tree = step.accept(builder)
        assert tree is not None

    def test_visit_wait_unchanged(self):
        ctx = _make_context()
        builder = KukaNodeFromStepBuilder(ctx)

        step = MissionStepWait(timeoutSecs=5.0)
        tree = step.accept(builder)
        assert tree is not None
