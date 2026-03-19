# SPDX-FileCopyrightText: 2026 Mappalink
#
# SPDX-License-Identifier: MIT

"""Async HTTP client for the KUKA.AMR Fleet Interface Manager API."""

import logging
import uuid
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class KukaFleetApi:
    """Client for the KUKA.AMR Fleet Interface Manager API.

    Authentication goes through the Interface Manager login endpoint at
    /interfaces/api/login (plain text password, returns a JWT token).
    All robot queries and commands go through /interfaces/api/amr/<endpoint>.
    """

    def __init__(self, base_url: str, username: str, password: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._username = username
        self._password = password
        self._client = httpx.AsyncClient(timeout=10.0)
        self._token: str | None = None

    async def login(self) -> None:
        """Authenticate with the KUKA Fleet Interface Manager.

        Uses plain text password (not MD5). The Interface Manager is a
        separate service from the Fleet Manager UI, with its own credentials.
        """
        resp = await self._client.post(
            f"{self._base_url}/interfaces/api/login",
            json={"username": self._username, "password": self._password},
        )
        resp.raise_for_status()
        data = resp.json()
        self._token = data["data"]["token"]
        self._client.headers["Authorization"] = self._token
        logger.info("Authenticated with KUKA Fleet Interface Manager")

    async def close(self) -> None:
        await self._client.aclose()

    # -- Standard Interface helpers ----------------------------------------

    async def _post(self, endpoint: str, json: Any = None) -> dict:
        """POST to /interfaces/api/amr/<endpoint>."""
        resp = await self._client.post(
            f"{self._base_url}/interfaces/api/amr/{endpoint}",
            json=json or {},
        )
        resp.raise_for_status()
        return resp.json()

    async def _get(self, endpoint: str) -> dict:
        """GET to /interfaces/api/amr/<endpoint>."""
        resp = await self._client.get(
            f"{self._base_url}/interfaces/api/amr/{endpoint}",
        )
        resp.raise_for_status()
        return resp.json()

    async def _post_with_params(self, endpoint: str, params: dict) -> dict:
        """POST to /interfaces/api/amr/<endpoint> with query params (no JSON body)."""
        resp = await self._client.post(
            f"{self._base_url}/interfaces/api/amr/{endpoint}",
            params=params,
        )
        resp.raise_for_status()
        return resp.json()

    # -- Read endpoints ----------------------------------------------------

    async def robot_query(self, robot_id: str | None = None) -> dict:
        """Query robot status. Pass robot_id for a single robot, or None for all."""
        body = {"robotId": robot_id} if robot_id else {}
        return await self._post("robotQuery", body)

    async def job_query(self, body: dict | None = None) -> dict:
        """Query active jobs/missions."""
        return await self._post("jobQuery", body or {})

    async def container_query(self) -> dict:
        """Query all containers with IN/INSERTED status."""
        return await self._post("containerQuery", {})

    async def area_query(self) -> dict:
        """Query WCS areas."""
        return await self._get("areaQuery")

    # -- Write endpoints ---------------------------------------------------

    async def submit_move_mission(
        self, robot_id: str, node_code: str, robot_model: str
    ) -> tuple[dict, str]:
        """Submit a MOVE mission to send the robot to a single node.

        Returns (api_response, mission_code).
        """
        return await self.submit_multi_move_mission(robot_id, [node_code], robot_model)

    async def submit_multi_move_mission(
        self, robot_id: str, node_codes: list[str], robot_model: str
    ) -> tuple[dict, str]:
        """Submit a MOVE mission with one or more nodes.

        Multiple nodes are sequenced as missionData entries — the KUKA Fleet
        Manager executes them in order within a single mission.

        Returns (api_response, mission_code).
        """
        mission_code = f"CONN-{uuid.uuid4().hex[:8]}"
        body = {
            "orgId": "UNIVERSAL",
            "requestId": str(uuid.uuid4()),
            "missionCode": mission_code,
            "missionType": "MOVE",
            "robotIds": [robot_id],
            "robotModels": [robot_model],
            "robotType": "LIFT",
            "priority": 1,
            "missionData": [
                {
                    "sequence": i + 1,
                    "position": node_code,
                    "type": "NODE_POINT",
                    "passStrategy": "AUTO",
                    "waitingMillis": 0,
                }
                for i, node_code in enumerate(node_codes)
            ],
        }
        resp = await self._post("submitMission", body)
        return resp, mission_code

    async def robot_lift(
        self,
        robot_id: str,
        container_code: str | None = None,
        move_lift: int | None = None,
    ) -> dict:
        """Lift a container.

        Args:
            robot_id: Robot ID.
            container_code: Container to lift. Omit for blind lift (just raise
                mechanism without tracking a container).
            move_lift: 1 = move to container and lift,
                0 = actuate lift in place (no repositioning).
                Default: 1 if container_code given, 0 if blind lift.
        """
        if move_lift is None:
            move_lift = 1 if container_code else 0
        body: dict = {"robotId": robot_id, "moveLift": move_lift}
        if container_code:
            body["containerCode"] = container_code
        return await self._post("robotLift", body)

    async def robot_move_carry(
        self, robot_id: str, container_code: str, target_node_code: str
    ) -> dict:
        """Move robot carrying a container to a target node.

        Unlike robotMove, this tells the Fleet Manager which container is
        being carried so it can apply the correct footprint, obstacle
        avoidance plan, and speed limits for the laden robot.
        """
        return await self._post(
            "robotMoveCarry",
            {
                "robotId": robot_id,
                "containerCode": container_code,
                "targetNodeCode": target_node_code,
            },
        )

    async def robot_drop(self, robot_id: str, node_code: str | None = None) -> dict:
        """Drop a container at a node.

        Args:
            robot_id: Robot ID.
            node_code: Target node. Omit to drop in place (lower mechanism
                at current position).
        """
        body: dict = {"robotId": robot_id}
        if node_code:
            body["nodeCode"] = node_code
        return await self._post("robotDrop", body)

    async def charge_robot(
        self,
        robot_id: str,
        target_level: int = 90,
        lowest_level: int = 5,
    ) -> dict:
        """Send robot to charge."""
        return await self._post(
            "chargeRobot",
            {
                "robotId": robot_id,
                "necessary": 1,
                "targetLevel": target_level,
                "lowestLevel": lowest_level,
            },
        )

    async def submit_mission(self, body: dict) -> dict:
        """Dispatch a mission (MOVE, RACK_MOVE, etc.)."""
        return await self._post("submitMission", body)

    async def cancel_mission(self, mission_code: str, cancel_mode: str = "FORCE") -> dict:
        """Cancel a mission."""
        return await self._post(
            "missionCancel",
            {
                "requestId": str(uuid.uuid4()),
                "missionCode": mission_code,
                "cancelMode": cancel_mode,
            },
        )

    async def pause_mission(
        self, *, mission_code: str | None = None, robot_id: str | None = None
    ) -> dict:
        """Pause a running mission (by mission code or robot ID)."""
        params: dict = {}
        if mission_code:
            params["missionCode"] = mission_code
        if robot_id:
            params["robotId"] = robot_id
        return await self._post_with_params("pauseMission", params)

    async def recover_mission(
        self, *, mission_code: str | None = None, robot_id: str | None = None
    ) -> dict:
        """Resume a paused mission (by mission code or robot ID)."""
        params: dict = {}
        if mission_code:
            params["missionCode"] = mission_code
        if robot_id:
            params["robotId"] = robot_id
        return await self._post_with_params("recoverMission", params)

    async def unlock_robot(self, robot_id: str) -> dict:
        """Unlock robot (abnormal recovery)."""
        return await self._post("unlockRobot", {"robotId": robot_id})

    # -- Map image ---------------------------------------------------------

    async def fetch_map_image(self, image_path: str) -> bytes | None:
        """Fetch SLAM map image from the KUKA fileserver (no auth required)."""
        url = f"{self._base_url}/fileserver{image_path}"
        resp = await self._client.get(url)
        if resp.status_code == 200:
            return resp.content
        logger.warning("Failed to fetch map image from %s: %s", url, resp.status_code)
        return None
