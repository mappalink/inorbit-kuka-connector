# SPDX-FileCopyrightText: 2026 Mappalink
#
# SPDX-License-Identifier: MIT

"""Edge-executor mission support for KUKA AMR connector.

Extends inorbit-edge-executor with KUKA-specific pause/resume/abort that
forward to the KUKA Fleet Interface Manager API.
"""

import json
import logging
from enum import Enum
from typing import Callable

from inorbit_connector.connector import CommandResultCode
from inorbit_edge_executor.datatypes import MissionRuntimeOptions
from inorbit_edge_executor.db import get_db
from inorbit_edge_executor.mission import Mission
from inorbit_edge_executor.worker_pool import WorkerPool

from .kuka_api import KukaFleetApi
from .mission.behavior_tree import KukaBehaviorTreeBuilderContext
from .mission.tree_builder import KukaTreeBuilder

logger = logging.getLogger(__name__)


class MissionScriptName(Enum):
    """Mission-related custom commands sent by InOrbit edge executor."""

    EXECUTE_MISSION_ACTION = "executeMissionAction"
    CANCEL_MISSION_ACTION = "cancelMissionAction"
    UPDATE_MISSION_ACTION = "updateMissionAction"


class KukaWorkerPool(WorkerPool):
    """WorkerPool that executes steps locally via KUKA API and forwards
    pause/resume/abort to the KUKA Fleet API."""

    def __init__(
        self,
        kuka_api: KukaFleetApi,
        get_kuka_mission_code: Callable[[], str | None],
        kuka_robot_id: str = "",
        robot_model: str = "",
        nodes: list[tuple[str, float, float]] | None = None,
        node_margin_m: float = 0.05,
        *args,
        **kwargs,
    ):
        self._kuka_api = kuka_api
        self._get_kuka_mission_code = get_kuka_mission_code
        self._kuka_robot_id = kuka_robot_id
        self._robot_model = robot_model
        self._nodes = nodes or []
        self._node_margin_m = node_margin_m
        super().__init__(behavior_tree_builder=KukaTreeBuilder(), *args, **kwargs)

    def create_builder_context(self) -> KukaBehaviorTreeBuilderContext:
        return KukaBehaviorTreeBuilderContext(
            kuka_api=self._kuka_api,
            kuka_robot_id=self._kuka_robot_id,
            robot_model=self._robot_model,
            nodes=self._nodes,
            node_margin_m=self._node_margin_m,
        )

    def prepare_builder_context(self, context, mission):
        super().prepare_builder_context(context, mission)
        # Attach mission code getter so abort nodes can cancel the active KUKA mission
        context._get_kuka_mission_code = self._get_kuka_mission_code

    async def pause_mission(self, mission_id):
        await super().pause_mission(mission_id)
        await self._kuka_api.pause_mission(robot_id=self._kuka_robot_id)

    async def resume_mission(self, mission_id):
        await super().resume_mission(mission_id)
        await self._kuka_api.recover_mission(robot_id=self._kuka_robot_id)

    async def abort_mission(self, mission_id):
        super().abort_mission(mission_id)
        code = self._get_kuka_mission_code()
        if code:
            await self._kuka_api.cancel_mission(code)
        else:
            logger.warning("No active KUKA mission code — skipping KUKA cancel")


class KukaMissionExecutor:
    """Handles edge-executor mission commands for a single KUKA robot."""

    def __init__(
        self,
        robot_id: str,
        inorbit_api,
        kuka_api: KukaFleetApi,
        get_kuka_mission_code: Callable[[], str | None],
        database_file: str | None = None,
        kuka_robot_id: str = "",
        robot_model: str = "",
        nodes: list[tuple[str, float, float]] | None = None,
        node_margin_m: float = 0.05,
    ):
        self._robot_id = robot_id
        self._inorbit_api = inorbit_api
        self._kuka_api = kuka_api
        self._get_kuka_mission_code = get_kuka_mission_code
        self._kuka_robot_id = kuka_robot_id
        self._robot_model = robot_model
        self._nodes = nodes or []
        self._node_margin_m = node_margin_m
        if database_file:
            if database_file == "dummy":
                self._database_file = "dummy"
            else:
                self._database_file = f"sqlite:{database_file}"
        else:
            self._database_file = f"sqlite:missions_{robot_id}.db"
        self._worker_pool: KukaWorkerPool | None = None
        self._initialized = False

    async def initialize(self):
        if self._initialized:
            return
        db = await get_db(self._database_file)
        self._worker_pool = KukaWorkerPool(
            kuka_api=self._kuka_api,
            get_kuka_mission_code=self._get_kuka_mission_code,
            kuka_robot_id=self._kuka_robot_id,
            robot_model=self._robot_model,
            nodes=self._nodes,
            node_margin_m=self._node_margin_m,
            api=self._inorbit_api,
            db=db,
        )
        await self._worker_pool.start()
        self._initialized = True
        logger.info("KUKA Mission Executor initialized")

    async def shutdown(self):
        if self._worker_pool:
            await self._worker_pool.shutdown()
            logger.info("KUKA Mission Executor shut down")

    async def handle_command(self, script_name: str, script_args: dict, options: dict) -> bool:
        """Route mission commands. Returns True if handled, False otherwise."""
        if not self._initialized:
            logger.warning("Mission executor not initialized, cannot handle commands")
            return False

        if script_name == MissionScriptName.EXECUTE_MISSION_ACTION.value:
            await self._handle_execute_mission_action(script_args, options)
            return True
        elif script_name == MissionScriptName.CANCEL_MISSION_ACTION.value:
            await self._handle_cancel_mission(script_args, options)
            return True
        elif script_name == MissionScriptName.UPDATE_MISSION_ACTION.value:
            await self._handle_update_mission_action(script_args, options)
            return True
        return False

    async def _handle_execute_mission_action(self, script_args: dict, options: dict) -> None:
        try:
            mission_id = script_args.get("missionId")
            mission_definition = json.loads(script_args.get("missionDefinition", "{}"))
            mission_args = json.loads(script_args.get("missionArgs", "{}"))
            mission_options_dict = json.loads(script_args.get("options", "{}"))

            mission = Mission(
                id=mission_id,
                robot_id=self._robot_id,
                definition=mission_definition,
                arguments=mission_args,
            )
            mission_runtime_options = MissionRuntimeOptions(**mission_options_dict)
            await self._worker_pool.submit_work(mission, mission_runtime_options)
            options["result_function"](CommandResultCode.SUCCESS)
        except json.JSONDecodeError as e:
            logger.error("Invalid JSON in mission definition: %s", e)
            options["result_function"](
                CommandResultCode.FAILURE,
                execution_status_details=f"Invalid JSON: {e}",
            )
        except Exception as e:
            logger.error("Failed to execute mission: %s", e)
            options["result_function"](
                CommandResultCode.FAILURE,
                execution_status_details=str(e),
            )

    async def _handle_cancel_mission(self, script_args: dict, options: dict) -> None:
        mission_id = script_args.get("missionId")
        logger.info("Handling cancelMission for mission %s", mission_id)
        try:
            result = await self._worker_pool.abort_mission(mission_id)
            if result is False:
                options["result_function"](CommandResultCode.FAILURE, "Mission not found")
            else:
                options["result_function"](CommandResultCode.SUCCESS)
        except Exception as e:
            logger.error("Failed to cancel mission %s: %s", mission_id, e)
            options["result_function"](
                CommandResultCode.FAILURE,
                execution_status_details=str(e),
            )

    async def _handle_update_mission_action(self, script_args: dict, options: dict) -> None:
        mission_id = script_args.get("missionId")
        action = script_args.get("action")
        logger.info("Handling updateMissionAction %s for mission %s", action, mission_id)
        try:
            if action == "pause":
                await self._worker_pool.pause_mission(mission_id)
            elif action == "resume":
                await self._worker_pool.resume_mission(mission_id)
            else:
                raise ValueError(f"Unknown action: {action}")
            options["result_function"](CommandResultCode.SUCCESS)
        except Exception as e:
            logger.error("Failed to update mission %s (action=%s): %s", mission_id, action, e)
            options["result_function"](
                CommandResultCode.FAILURE,
                execution_status_details=str(e),
            )
