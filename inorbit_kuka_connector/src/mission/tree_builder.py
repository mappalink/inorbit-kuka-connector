# SPDX-FileCopyrightText: 2026 Mappalink
#
# SPDX-License-Identifier: MIT

"""Tree builder for KUKA missions with local API execution.

Consecutive move-to-node steps are merged into a single KUKA submitMission
call with multiple missionData entries, so the KUKA Fleet Manager can
sequence them in one mission instead of N separate ones.
"""

from __future__ import annotations

import logging

from inorbit_edge_executor.behavior_tree import (
    BehaviorTree,
    BehaviorTreeErrorHandler,
    BehaviorTreeSequential,
    DefaultTreeBuilder,
    MissionCompletedNode,
    MissionInProgressNode,
    MissionPausedNode,
)
from inorbit_edge_executor.datatypes import (
    MissionStepPoseWaypoint,
    MissionStepRunAction,
)
from inorbit_edge_executor.inorbit import MissionStatus

from .behavior_tree import (
    KukaBehaviorTreeBuilderContext,
    KukaMissionAbortedNode,
    KukaMultiMoveNode,
    KukaNodeFromStepBuilder,
    WaitForKukaCompletionNode,
    _find_nearest_node,
)

logger = logging.getLogger(__name__)


def _extract_node_code(step, context: KukaBehaviorTreeBuilderContext) -> str | None:
    """Try to extract a KUKA node code from a mission step.

    Returns the node code if this step is a move-to-node, or None otherwise.
    """
    if isinstance(step, MissionStepRunAction):
        action_id = step.action_id
        arguments = step.arguments or {}
        if action_id == "kuka-move-to-node":
            return arguments.get("node_code")

    if isinstance(step, MissionStepPoseWaypoint):
        wp = step.waypoint
        node_code, distance = _find_nearest_node(context.nodes, wp.x, wp.y)
        if node_code and distance <= context.node_margin_m:
            return node_code

    return None


class KukaTreeBuilder(DefaultTreeBuilder):
    """Tree builder that uses KUKA-specific step nodes for local execution.

    Consecutive move-to-node steps are merged into a single KukaMultiMoveNode
    so the KUKA Fleet Manager handles the full route in one mission.
    """

    def __init__(self, **kwargs):
        super().__init__(step_builder_factory=KukaNodeFromStepBuilder, **kwargs)

    def build_tree_for_mission(self, context: KukaBehaviorTreeBuilderContext) -> BehaviorTree:
        mission = context.mission
        tree = BehaviorTreeSequential(label=f"mission {mission.id}")

        tree.add_node(MissionInProgressNode(context, label="mission start"))

        steps = list(mission.definition.steps)
        step_builder = KukaNodeFromStepBuilder(context)
        i = 0

        while i < len(steps):
            # Try to collect a run of consecutive move-to-node steps
            node_code = _extract_node_code(steps[i], context)
            if node_code:
                node_codes = [node_code]
                # Collect the max timeout from the batch
                max_timeout = getattr(steps[i], "timeout_secs", None) or 120.0
                j = i + 1
                while j < len(steps):
                    next_code = _extract_node_code(steps[j], context)
                    if not next_code:
                        break
                    node_codes.append(next_code)
                    step_timeout = getattr(steps[j], "timeout_secs", None) or 120.0
                    max_timeout = max(max_timeout, step_timeout)
                    j += 1

                # Build a single multi-move node for the batch
                route = " -> ".join(node_codes)
                total_timeout = max_timeout * len(node_codes)
                sequence = BehaviorTreeSequential(label=f"Move {route}")
                sequence.add_node(
                    KukaMultiMoveNode(
                        context,
                        node_codes=node_codes,
                        label=f"submitMission MOVE {route}",
                    )
                )
                sequence.add_node(
                    WaitForKukaCompletionNode(
                        context,
                        timeout_secs=total_timeout,
                        label=f"Wait for completion ({len(node_codes)} nodes)",
                    )
                )
                tree.add_node(sequence)
                logger.info(
                    "Merged %d move steps into single KUKA mission: %s",
                    len(node_codes),
                    route,
                )
                i = j
            else:
                # Non-move step — process individually
                try:
                    node = steps[i].accept(step_builder)
                except Exception as e:
                    raise RuntimeError(f"Error building step #{i} [{steps[i]}]: {e}") from e
                if node:
                    tree.add_node(node)
                i += 1

        tree.add_node(MissionCompletedNode(context, label="mission completed"))

        # Error handling
        on_error = BehaviorTreeSequential(label="error handlers")
        on_error.add_node(
            KukaMissionAbortedNode(context, status=MissionStatus.error, label="mission aborted")
        )

        on_cancel = BehaviorTreeSequential(label="cancel handlers")
        on_cancel.add_node(
            KukaMissionAbortedNode(context, status=MissionStatus.ok, label="mission cancelled")
        )

        on_pause = BehaviorTreeSequential(label="pause handlers")
        on_pause.add_node(MissionPausedNode(context, label="mission paused"))

        tree = BehaviorTreeErrorHandler(
            context,
            tree,
            on_error,
            on_cancel,
            on_pause,
            context.error_context,
            label=f"mission {mission.id}",
        )

        return tree
