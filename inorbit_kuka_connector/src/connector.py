# SPDX-FileCopyrightText: 2026 InOrbit, Inc.
#
# SPDX-License-Identifier: MIT

"""KUKA AMR connector — bridges KUKA.AMR Fleet to InOrbit Cloud."""

import json
import logging
import math

from inorbit_connector.connector import Connector, CommandResultCode
from inorbit_connector.models import MapConfigTemp
from inorbit_edge.robot import COMMAND_CUSTOM_COMMAND, COMMAND_MESSAGE, COMMAND_NAV_GOAL

from .config.models import ConnectorConfig
from .kuka_api import KukaFleetApi

logger = logging.getLogger(__name__)

# KUKA robot status codes
KUKA_STATUS = {
    1: "Departure",
    2: "Offline",
    3: "Idle",
    4: "Executing",
    5: "Charging",
    6: "Updating",
    7: "Abnormal",
}


class KukaAmrConnector(Connector):
    def __init__(self, robot_id: str, config: ConnectorConfig) -> None:
        super().__init__(robot_id=robot_id, config=config)
        cfg = config.connector_config
        self._kuka_robot_id = cfg.kuka_robot_id
        self._api = KukaFleetApi(
            base_url=cfg.fleet_url,
            username=cfg.username,
            password=cfg.password,
        )
        self._map_image_path = cfg.map_image_path
        self._map_resolution = cfg.map_resolution
        self._nodes = self._load_nodes(cfg.nodes_file) if cfg.nodes_file else []
        self._node_margin_m = cfg.node_margin_cm / 100.0

    # -- Lifecycle ---------------------------------------------------------

    async def _connect(self) -> None:
        await self._api.login()
        logger.info("Connected to KUKA Fleet Manager for robot %s", self._kuka_robot_id)

    async def _disconnect(self) -> None:
        await self._api.close()
        logger.info("Disconnected from KUKA Fleet Manager")

    # -- Main loop (~1 Hz) ------------------------------------------------

    async def _execution_loop(self) -> None:
        try:
            data = await self._api.robot_query(self._kuka_robot_id)
        except Exception as e:
            logger.error("robotQuery failed: %s", e)
            return

        if not data.get("success") or not data.get("data"):
            return

        robot = data["data"][0]

        # Coordinates: millimeter strings -> meters
        x_m = float(robot.get("x", 0)) / 1000.0
        y_m = float(robot.get("y", 0)) / 1000.0
        yaw_rad = math.radians(float(robot.get("robotOrientation", 0)))
        frame_id = robot.get("mapCode", "")

        self.publish_pose(x=x_m, y=y_m, yaw=yaw_rad, frame_id=frame_id)

        self.publish_key_values(
            battery_percent=robot.get("batteryLevel"),
            robot_status=robot.get("status"),
            robot_status_text=KUKA_STATUS.get(robot.get("status"), "Unknown"),
            occupy_status=robot.get("occupyStatus"),
            lift_status=robot.get("liftStatus"),
            location_reliability=robot.get("reliability"),
            error_message=robot.get("errorMessage", ""),
            container_code=robot.get("containerCode", ""),
            node_code=robot.get("nodeCode", ""),
            node_label=robot.get("nodeLabel", ""),
            os_version=robot.get("karOsVersion", ""),
            mileage=robot.get("mileage", ""),
            run_time=robot.get("runTime", ""),
            motor_temp_left=robot.get("leftMotorTemperature", ""),
            motor_temp_right=robot.get("rightMotorTemperature", ""),
            motor_temp_lift=robot.get("liftMtrTemp", ""),
            lift_times=robot.get("liftTimes", 0),
            mission_code=robot.get("missionCode", ""),
        )

    # -- Command handler ---------------------------------------------------

    async def _inorbit_command_handler(self, command_name, args, options):
        result_fn = options["result_function"]

        if command_name == COMMAND_NAV_GOAL:
            await self._handle_nav_goal(args[0], result_fn)

        elif command_name == COMMAND_CUSTOM_COMMAND:
            script_name = args[0]
            script_args = args[1] if len(args) > 1 else ""
            await self._handle_custom_command(script_name, script_args, result_fn)

        elif command_name == COMMAND_MESSAGE:
            msg = args[0]
            await self._handle_message(msg, result_fn)

        else:
            logger.warning("Unhandled command type: %s", command_name)
            result_fn(CommandResultCode.FAILURE)

    async def _handle_custom_command(self, script_name, script_args, result_fn):
        try:
            if script_name == "move_to_node":
                node_code = self._parse_arg(script_args, "--node_code")
                resp = await self._api.robot_move(self._kuka_robot_id, node_code)
                self._report_result(resp, result_fn)

            elif script_name == "lift":
                container_code = self._parse_arg(script_args, "--container_code")
                resp = await self._api.robot_lift(self._kuka_robot_id, container_code)
                self._report_result(resp, result_fn)

            elif script_name == "drop":
                node_code = self._parse_arg(script_args, "--node_code")
                resp = await self._api.robot_drop(self._kuka_robot_id, node_code)
                self._report_result(resp, result_fn)

            elif script_name == "charge":
                resp = await self._api.charge_robot(self._kuka_robot_id)
                self._report_result(resp, result_fn)

            elif script_name == "cancel_mission":
                mission_code = self._parse_arg(script_args, "--mission_code")
                resp = await self._api.cancel_mission(mission_code)
                self._report_result(resp, result_fn)

            elif script_name == "pause_mission":
                mission_code = self._parse_arg(script_args, "--mission_code")
                resp = await self._api.pause_mission(mission_code)
                self._report_result(resp, result_fn)

            elif script_name == "resume_mission":
                mission_code = self._parse_arg(script_args, "--mission_code")
                resp = await self._api.recover_mission(mission_code)
                self._report_result(resp, result_fn)

            elif script_name == "unlock":
                resp = await self._api.unlock_robot(self._kuka_robot_id)
                self._report_result(resp, result_fn)

            else:
                logger.warning("Unknown custom command: %s", script_name)
                result_fn(CommandResultCode.FAILURE)

        except Exception as e:
            logger.error("Custom command '%s' failed: %s", script_name, e)
            result_fn(CommandResultCode.FAILURE)

    async def _handle_nav_goal(self, pose, result_fn):
        """Resolve NAV_GOAL coordinates to the nearest KUKA node."""
        x, y = float(pose["x"]), float(pose["y"])
        node_code, distance = self._find_nearest_node(x, y)
        if not node_code:
            msg = f"NAV_GOAL ({x:.3f}, {y:.3f}) rejected: no nodes loaded"
            logger.warning(msg)
            result_fn(CommandResultCode.FAILURE, execution_status_details=msg)
            return
        if distance > self._node_margin_m:
            msg = (
                f"NAV_GOAL ({x:.3f}, {y:.3f}) rejected: nearest node {node_code} "
                f"is {distance:.3f}m away, exceeds margin of "
                f"{self._node_margin_m * 100:.0f}cm"
            )
            logger.error(msg)
            result_fn(CommandResultCode.FAILURE, execution_status_details=msg)
            return
        logger.info(
            "NAV_GOAL (%.3f, %.3f) -> node %s (%.3fm away) [DRY RUN]",
            x,
            y,
            node_code,
            distance,
        )
        # TODO: enable when verified
        # resp = await self._api.robot_move(self._kuka_robot_id, node_code)
        # self._report_result(resp, result_fn)
        result_fn(CommandResultCode.SUCCESS)

    def _find_nearest_node(self, x: float, y: float) -> tuple[str | None, float]:
        """Find the nearest KUKA node to the given coordinates (meters).

        Returns (node_uuid, distance) or (None, 0) if no nodes loaded.
        """
        if not self._nodes:
            return None, 0.0

        best_node, best_dist = None, float("inf")
        for node_uuid, nx, ny in self._nodes:
            dist = math.hypot(x - nx, y - ny)
            if dist < best_dist:
                best_node, best_dist = node_uuid, dist

        return best_node, best_dist

    async def _handle_message(self, msg, result_fn):
        # KUKA pause/resume only works on active missions.
        # For now, log and report failure since we'd need the active mission code.
        logger.info("Message command received: %s (not implemented)", msg)
        result_fn(CommandResultCode.FAILURE)

    # -- Map ---------------------------------------------------------------

    async def fetch_map(self, frame_id: str) -> MapConfigTemp | None:
        """Fetch the SLAM map image from the KUKA fileserver."""
        if not self._map_image_path:
            logger.warning("No map_image_path configured, cannot fetch map")
            return None

        image_bytes = await self._api.fetch_map_image(self._map_image_path)
        if not image_bytes:
            return None

        logger.info("Fetched map image for frame_id=%s", frame_id)
        return MapConfigTemp(
            image=image_bytes,
            map_id=frame_id,
            map_label=f"KUKA {frame_id}",
            origin_x=0.0,
            origin_y=0.0,
            resolution=self._map_resolution,
        )

    # -- Helpers -----------------------------------------------------------

    @staticmethod
    def _load_nodes(path: str) -> list[tuple[str, float, float]]:
        """Load node positions from a KUKA graph export JSON.

        Returns list of (node_uuid, x_meters, y_meters).
        """
        with open(path) as f:
            data = json.load(f)
        nodes = []
        for node in data["floorList"][0]["nodeList"]:
            nodes.append(
                (
                    node["nodeUuid"],
                    node["xCoordinate"],
                    node["yCoordinate"],
                )
            )
        logger.info("Loaded %d nodes from %s", len(nodes), path)
        return nodes

    @staticmethod
    def _parse_arg(script_args: str, arg_name: str) -> str:
        """Parse a --key value argument from the script args string."""
        parts = script_args.split(arg_name)
        if len(parts) < 2:
            raise ValueError(f"Missing argument: {arg_name}")
        return parts[1].strip().split()[0]

    @staticmethod
    def _report_result(resp: dict, result_fn) -> None:
        if resp.get("success"):
            result_fn(CommandResultCode.SUCCESS)
        else:
            logger.warning("KUKA API returned failure: %s", resp)
            result_fn(CommandResultCode.FAILURE)
