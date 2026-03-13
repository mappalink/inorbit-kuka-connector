# SPDX-FileCopyrightText: 2026 Mappalink
#
# SPDX-License-Identifier: MIT

"""Custom behavior tree nodes for executing KUKA missions locally.

Instead of round-tripping each step through the InOrbit cloud API, these nodes
call the KUKA Fleet Interface Manager REST API directly and poll robotQuery /
jobQuery for completion.

Step mapping:
    poseWaypoint  -> resolve nearest KUKA node -> robotMove -> poll until idle
    runAction     -> map actionId to KUKA API call -> poll until idle
"""

from __future__ import annotations

import asyncio
import logging
import math
from enum import StrEnum
from typing import Optional

from inorbit_edge_executor.behavior_tree import (
    BehaviorTree,
    BehaviorTreeBuilderContext,
    BehaviorTreeSequential,
    MissionAbortedNode,
    NodeFromStepBuilder,
    register_accepted_node_types,
)
from inorbit_edge_executor.datatypes import (
    MissionStepPoseWaypoint,
    MissionStepRunAction,
)
from inorbit_edge_executor.inorbit import MissionStatus

from inorbit_kuka_connector.src.kuka_api import KukaFleetApi

logger = logging.getLogger(__name__)

# KUKA robot status codes (from robotQuery)
_STATUS_IDLE = 3
_STATUS_EXECUTING = 4
_STATUS_CHARGING = 5
_STATUS_ABNORMAL = 7

# KUKA job status codes (from jobQuery)
_JOB_COMPLETE = 30
_JOB_CANCELLED = 31
_JOB_WARNING = 50
_JOB_STARTUP_ERROR = 60

# Polling interval for robotQuery checks
_POLL_INTERVAL_SECS = 1.0

# Known InOrbit action IDs that we handle locally
ACTION_NAVIGATE_TO = "NavigateTo-000000"


class SharedMemoryKeys(StrEnum):
    KUKA_ERROR_MESSAGE = "kuka_error_message"


class KukaBehaviorTreeBuilderContext(BehaviorTreeBuilderContext):
    """Extended context carrying KUKA API, robot ID, and node graph."""

    def __init__(
        self,
        kuka_api: KukaFleetApi,
        kuka_robot_id: str,
        nodes: list[tuple[str, float, float]],
        node_margin_m: float,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._kuka_api = kuka_api
        self._kuka_robot_id = kuka_robot_id
        self._nodes = nodes
        self._node_margin_m = node_margin_m

    @property
    def kuka_api(self) -> KukaFleetApi:
        return self._kuka_api

    @property
    def kuka_robot_id(self) -> str:
        return self._kuka_robot_id

    @property
    def nodes(self) -> list[tuple[str, float, float]]:
        return self._nodes

    @property
    def node_margin_m(self) -> float:
        return self._node_margin_m


def _find_nearest_node(
    nodes: list[tuple[str, float, float]], x: float, y: float
) -> tuple[str | None, float]:
    """Find the nearest KUKA node to the given coordinates (meters)."""
    if not nodes:
        return None, 0.0
    best_node, best_dist = None, float("inf")
    for node_uuid, nx, ny in nodes:
        dist = math.hypot(x - nx, y - ny)
        if dist < best_dist:
            best_node, best_dist = node_uuid, dist
    return best_node, best_dist


# ---------------------------------------------------------------------------
# Core polling node — waits for KUKA robot to finish its current task
# ---------------------------------------------------------------------------


class WaitForKukaCompletionNode(BehaviorTree):
    """Polls robotQuery until the robot leaves Executing state.

    Succeeds when status is Idle or Charging.
    Fails on Abnormal or timeout.
    """

    def __init__(
        self,
        context: KukaBehaviorTreeBuilderContext,
        timeout_secs: Optional[float] = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._kuka_api = context.kuka_api
        self._kuka_robot_id = context.kuka_robot_id
        self._shared_memory = context.shared_memory
        self._timeout_secs = timeout_secs

        self._shared_memory.add(SharedMemoryKeys.KUKA_ERROR_MESSAGE, None)

    async def _execute(self):
        logger.info("Waiting for KUKA robot %s to complete task", self._kuka_robot_id)
        elapsed = 0.0

        while True:
            if self._timeout_secs and elapsed >= self._timeout_secs:
                error_msg = (
                    f"KUKA robot {self._kuka_robot_id} timed out after {self._timeout_secs}s"
                )
                logger.error(error_msg)
                self._shared_memory.set(SharedMemoryKeys.KUKA_ERROR_MESSAGE, error_msg)
                raise RuntimeError(error_msg)

            try:
                data = await self._kuka_api.robot_query(self._kuka_robot_id)
                if data.get("success") and data.get("data"):
                    robot = data["data"][0]
                    status = robot.get("status")

                    if status in (_STATUS_IDLE, _STATUS_CHARGING):
                        logger.info(
                            "KUKA robot %s completed (status=%s)",
                            self._kuka_robot_id,
                            status,
                        )
                        return

                    if status == _STATUS_ABNORMAL:
                        error_msg = (
                            f"KUKA robot {self._kuka_robot_id} entered Abnormal state: "
                            f"{robot.get('errorMessage', '')}"
                        )
                        logger.error(error_msg)
                        self._shared_memory.set(SharedMemoryKeys.KUKA_ERROR_MESSAGE, error_msg)
                        raise RuntimeError(error_msg)

                    logger.debug(
                        "KUKA robot %s status=%s, waiting...",
                        self._kuka_robot_id,
                        status,
                    )
            except RuntimeError:
                raise
            except Exception as e:
                logger.warning("robotQuery poll failed: %s", e)

            await asyncio.sleep(_POLL_INTERVAL_SECS)
            elapsed += _POLL_INTERVAL_SECS

    @classmethod
    def from_object(cls, context, timeout_secs=None, **kwargs):
        return WaitForKukaCompletionNode(context, timeout_secs=timeout_secs, **kwargs)


# ---------------------------------------------------------------------------
# Move to node
# ---------------------------------------------------------------------------


class KukaMoveToNodeNode(BehaviorTree):
    """Calls robotMove to send the robot to a KUKA node."""

    def __init__(
        self,
        context: KukaBehaviorTreeBuilderContext,
        node_code: str,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._kuka_api = context.kuka_api
        self._kuka_robot_id = context.kuka_robot_id
        self._shared_memory = context.shared_memory
        self._node_code = node_code

        self._shared_memory.add(SharedMemoryKeys.KUKA_ERROR_MESSAGE, None)

    async def _execute(self):
        logger.info(
            "Sending KUKA robot %s to node %s",
            self._kuka_robot_id,
            self._node_code,
        )
        try:
            resp = await self._kuka_api.robot_move(self._kuka_robot_id, self._node_code)
            if not resp.get("success"):
                error_msg = f"robotMove failed: {resp}"
                logger.error(error_msg)
                self._shared_memory.set(SharedMemoryKeys.KUKA_ERROR_MESSAGE, error_msg)
                raise RuntimeError(error_msg)
        except RuntimeError:
            raise
        except Exception as e:
            error_msg = f"robotMove to {self._node_code} failed: {e}"
            logger.error(error_msg)
            self._shared_memory.set(SharedMemoryKeys.KUKA_ERROR_MESSAGE, error_msg)
            raise RuntimeError(error_msg) from e

    def dump_object(self):
        obj = super().dump_object()
        obj["node_code"] = self._node_code
        return obj

    @classmethod
    def from_object(cls, context, node_code, **kwargs):
        return KukaMoveToNodeNode(context, node_code=node_code, **kwargs)


# ---------------------------------------------------------------------------
# Generic KUKA action node (lift, drop, move_carry, charge)
# ---------------------------------------------------------------------------

# Maps action names to (api_method_name, required_arg_keys)
_KUKA_ACTION_MAP = {
    "lift": ("robot_lift", []),
    "container_lift": ("robot_lift", ["container_code"]),
    "drop": ("robot_drop", []),
    "container_drop": ("robot_drop", ["node_code"]),
    "move_carry": ("robot_move_carry", ["container_code", "node_code"]),
    "charge": ("charge_robot", []),
    "unlock": ("unlock_robot", []),
}


class KukaActionNode(BehaviorTree):
    """Executes a KUKA API action (lift, drop, move_carry, charge) directly."""

    def __init__(
        self,
        context: KukaBehaviorTreeBuilderContext,
        action_name: str,
        action_args: dict,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._kuka_api = context.kuka_api
        self._kuka_robot_id = context.kuka_robot_id
        self._shared_memory = context.shared_memory
        self._action_name = action_name
        self._action_args = action_args

        self._shared_memory.add(SharedMemoryKeys.KUKA_ERROR_MESSAGE, None)

    async def _execute(self):
        if self._action_name not in _KUKA_ACTION_MAP:
            raise RuntimeError(f"Unknown KUKA action: {self._action_name}")

        method_name, required_keys = _KUKA_ACTION_MAP[self._action_name]
        api_method = getattr(self._kuka_api, method_name)

        # Build positional args: always starts with robot_id
        call_args = [self._kuka_robot_id]
        for key in required_keys:
            if key not in self._action_args:
                raise RuntimeError(f"KUKA action '{self._action_name}' missing argument: {key}")
            call_args.append(self._action_args[key])

        logger.info(
            "Executing KUKA action %s on robot %s: %s",
            self._action_name,
            self._kuka_robot_id,
            self._action_args,
        )

        try:
            resp = await api_method(*call_args)
            if not resp.get("success"):
                error_msg = f"KUKA {self._action_name} failed: {resp}"
                logger.error(error_msg)
                self._shared_memory.set(SharedMemoryKeys.KUKA_ERROR_MESSAGE, error_msg)
                raise RuntimeError(error_msg)
        except RuntimeError:
            raise
        except Exception as e:
            error_msg = f"KUKA {self._action_name} failed: {e}"
            logger.error(error_msg)
            self._shared_memory.set(SharedMemoryKeys.KUKA_ERROR_MESSAGE, error_msg)
            raise RuntimeError(error_msg) from e

    def dump_object(self):
        obj = super().dump_object()
        obj["action_name"] = self._action_name
        obj["action_args"] = self._action_args
        return obj

    @classmethod
    def from_object(cls, context, action_name, action_args, **kwargs):
        return KukaActionNode(context, action_name=action_name, action_args=action_args, **kwargs)


# ---------------------------------------------------------------------------
# Abort node — cancels active KUKA mission before reporting abort
# ---------------------------------------------------------------------------


class KukaMissionAbortedNode(MissionAbortedNode):
    """Extended abort that cancels the active KUKA mission."""

    def __init__(
        self,
        context: KukaBehaviorTreeBuilderContext,
        status: MissionStatus = MissionStatus.error,
        **kwargs,
    ):
        super().__init__(context, status, **kwargs)
        self._kuka_api = context.kuka_api
        self._kuka_robot_id = context.kuka_robot_id
        self._shared_memory = context.shared_memory
        self._get_kuka_mission_code = getattr(context, "_get_kuka_mission_code", None)

    async def _execute(self):
        error_message = self._shared_memory.get(SharedMemoryKeys.KUKA_ERROR_MESSAGE)
        if error_message:
            logger.error("KUKA mission aborted: %s", error_message)

        # Try to cancel the active KUKA mission
        if self._get_kuka_mission_code:
            code = self._get_kuka_mission_code()
            if code:
                try:
                    await self._kuka_api.cancel_mission(code)
                    logger.info("Cancelled KUKA mission %s", code)
                except Exception as e:
                    logger.warning("Failed to cancel KUKA mission %s: %s", code, e)

        await super()._execute()

    @classmethod
    def from_object(cls, context, status, **kwargs):
        return KukaMissionAbortedNode(context, MissionStatus(status), **kwargs)


# ---------------------------------------------------------------------------
# Step builder — maps InOrbit mission steps to KUKA BT nodes
# ---------------------------------------------------------------------------


class KukaNodeFromStepBuilder(NodeFromStepBuilder):
    """Builds KUKA-specific behavior tree nodes from mission steps."""

    def __init__(self, context: KukaBehaviorTreeBuilderContext):
        super().__init__(context)
        self._kuka_context = context

    def visit_pose_waypoint(self, step: MissionStepPoseWaypoint) -> BehaviorTree:
        """Resolve waypoint x,y to the nearest KUKA node, then move locally."""
        wp = step.waypoint
        node_code, distance = _find_nearest_node(self._kuka_context.nodes, wp.x, wp.y)

        if not node_code:
            raise RuntimeError(
                f"Cannot build move step: no KUKA nodes loaded for "
                f"waypoint ({wp.x:.3f}, {wp.y:.3f})"
            )

        if distance > self._kuka_context.node_margin_m:
            raise RuntimeError(
                f"Waypoint ({wp.x:.3f}, {wp.y:.3f}) too far from nearest "
                f"node {node_code} ({distance:.3f}m > "
                f"{self._kuka_context.node_margin_m:.3f}m margin)"
            )

        logger.info(
            "Waypoint (%.3f, %.3f) -> KUKA node %s (%.3fm away)",
            wp.x,
            wp.y,
            node_code,
            distance,
        )

        sequence = BehaviorTreeSequential(label=step.label or f"Move to {node_code}")
        sequence.add_node(
            KukaMoveToNodeNode(
                self._kuka_context,
                node_code=node_code,
                label=f"robotMove to {node_code}",
            )
        )
        sequence.add_node(
            WaitForKukaCompletionNode(
                self._kuka_context,
                timeout_secs=step.timeout_secs,
                label=f"Wait for arrival at {node_code}",
            )
        )
        return sequence

    def visit_run_action(self, step: MissionStepRunAction) -> BehaviorTree:
        """Map known KUKA actions to local API calls."""
        action_id = step.action_id
        arguments = step.arguments or {}

        # NavigateTo actions also come through as runAction with a pose
        if action_id == ACTION_NAVIGATE_TO:
            pose = arguments.get("pose", {})
            x = float(pose.get("x", 0))
            y = float(pose.get("y", 0))
            node_code, distance = _find_nearest_node(self._kuka_context.nodes, x, y)
            if not node_code:
                raise RuntimeError("Cannot resolve NavigateTo: no KUKA nodes loaded")
            if distance > self._kuka_context.node_margin_m:
                raise RuntimeError(
                    f"NavigateTo ({x:.3f}, {y:.3f}) too far from nearest "
                    f"node {node_code} ({distance:.3f}m)"
                )

            sequence = BehaviorTreeSequential(label=step.label or f"Navigate to {node_code}")
            sequence.add_node(
                KukaMoveToNodeNode(
                    self._kuka_context,
                    node_code=node_code,
                    label=f"robotMove to {node_code}",
                )
            )
            sequence.add_node(
                WaitForKukaCompletionNode(
                    self._kuka_context,
                    timeout_secs=step.timeout_secs,
                    label=f"Wait for arrival at {node_code}",
                )
            )
            return sequence

        # Map known KUKA actions
        if action_id in _KUKA_ACTION_MAP:
            sequence = BehaviorTreeSequential(label=step.label or f"KUKA {action_id}")
            sequence.add_node(
                KukaActionNode(
                    self._kuka_context,
                    action_name=action_id,
                    action_args=arguments,
                    label=f"Execute {action_id}",
                )
            )
            sequence.add_node(
                WaitForKukaCompletionNode(
                    self._kuka_context,
                    timeout_secs=step.timeout_secs,
                    label=f"Wait for {action_id} completion",
                )
            )
            return sequence

        # Unknown action — fall back to default (cloud round-trip)
        logger.warning("Unknown action '%s' — falling back to cloud execution", action_id)
        return super().visit_run_action(step)


# Register node types for serialization/deserialization (crash recovery)
kuka_node_types = [
    KukaMoveToNodeNode,
    KukaActionNode,
    WaitForKukaCompletionNode,
    KukaMissionAbortedNode,
]
register_accepted_node_types(kuka_node_types)
