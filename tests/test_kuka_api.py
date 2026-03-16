# SPDX-FileCopyrightText: 2026 Mappalink
#
# SPDX-License-Identifier: MIT

"""Tests for inorbit_kuka_connector.src.kuka_api using pytest-httpx."""

from __future__ import annotations

import json

import pytest
import pytest_asyncio

from inorbit_kuka_connector.src.kuka_api import KukaFleetApi


BASE_URL = "http://kuka-fleet:5000"


@pytest.fixture()
def api():
    return KukaFleetApi(base_url=BASE_URL, username="admin", password="secret")


@pytest_asyncio.fixture(autouse=True)
async def _cleanup_api(api):
    yield
    await api.close()


# -- Login -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_sets_token(api, httpx_mock):
    httpx_mock.add_response(
        url=f"{BASE_URL}/interfaces/api/login",
        json={"data": {"token": "jwt-abc-123"}},
    )
    await api.login()
    assert api._token == "jwt-abc-123"
    assert api._client.headers["Authorization"] == "jwt-abc-123"


# -- Read endpoints --------------------------------------------------------


@pytest.mark.asyncio
async def test_robot_query(api, httpx_mock):
    httpx_mock.add_response(
        url=f"{BASE_URL}/interfaces/api/amr/robotQuery",
        json={"success": True, "data": [{"x": "28247.0", "y": "12001.0"}]},
    )
    result = await api.robot_query("1")
    assert result["success"] is True
    assert result["data"][0]["x"] == "28247.0"

    req = httpx_mock.get_request()
    assert json.loads(req.content) == {"robotId": "1"}


@pytest.mark.asyncio
async def test_job_query_by_robot(api, httpx_mock):
    httpx_mock.add_response(
        url=f"{BASE_URL}/interfaces/api/amr/jobQuery",
        json={
            "success": True,
            "data": [
                {
                    "jobCode": "T000096284",
                    "robotId": "1",
                    "status": 20,
                    "workflowName": "Carry01",
                    "targetCellCode": "SITE-001-90",
                }
            ],
        },
    )
    result = await api.job_query({"robotId": "1", "limit": 5})
    assert result["success"] is True
    assert result["data"][0]["status"] == 20

    req = httpx_mock.get_request()
    body = json.loads(req.content)
    assert body["robotId"] == "1"
    assert body["limit"] == 5


@pytest.mark.asyncio
async def test_job_query_empty(api, httpx_mock):
    httpx_mock.add_response(
        url=f"{BASE_URL}/interfaces/api/amr/jobQuery",
        json={"success": True, "data": []},
    )
    result = await api.job_query({})
    assert result["data"] == []


@pytest.mark.asyncio
async def test_robot_query_all(api, httpx_mock):
    httpx_mock.add_response(
        url=f"{BASE_URL}/interfaces/api/amr/robotQuery",
        json={"success": True, "data": []},
    )
    await api.robot_query()

    req = httpx_mock.get_request()
    assert req.content == b"{}"


# -- Write endpoints -------------------------------------------------------


@pytest.mark.asyncio
async def test_robot_move(api, httpx_mock):
    httpx_mock.add_response(
        url=f"{BASE_URL}/interfaces/api/amr/robotMove",
        json={"success": True},
    )
    result = await api.robot_move("1", "SITE-001-40")
    assert result["success"] is True

    req = httpx_mock.get_request()
    assert json.loads(req.content) == {"robotId": "1", "nodeCode": "SITE-001-40"}


@pytest.mark.asyncio
async def test_robot_lift_with_container(api, httpx_mock):
    httpx_mock.add_response(
        url=f"{BASE_URL}/interfaces/api/amr/robotLift",
        json={"success": True},
    )
    result = await api.robot_lift("1", "C001")
    assert result["success"] is True

    req = httpx_mock.get_request()
    body = json.loads(req.content)
    assert body == {"robotId": "1", "containerCode": "C001", "moveLift": 1}


@pytest.mark.asyncio
async def test_robot_lift_blind(api, httpx_mock):
    """Blind lift — no container code, just raise mechanism."""
    httpx_mock.add_response(
        url=f"{BASE_URL}/interfaces/api/amr/robotLift",
        json={"success": True},
    )
    result = await api.robot_lift("1")
    assert result["success"] is True

    req = httpx_mock.get_request()
    body = json.loads(req.content)
    assert body == {"robotId": "1", "moveLift": 0}
    assert "containerCode" not in body


@pytest.mark.asyncio
async def test_robot_lift_in_place(api, httpx_mock):
    """In-place lift — moveLift=0, no repositioning."""
    httpx_mock.add_response(
        url=f"{BASE_URL}/interfaces/api/amr/robotLift",
        json={"success": True},
    )
    result = await api.robot_lift("1", "C001", move_lift=0)
    assert result["success"] is True

    req = httpx_mock.get_request()
    body = json.loads(req.content)
    assert body == {"robotId": "1", "containerCode": "C001", "moveLift": 0}


@pytest.mark.asyncio
async def test_robot_move_carry(api, httpx_mock):
    httpx_mock.add_response(
        url=f"{BASE_URL}/interfaces/api/amr/robotMoveCarry",
        json={"success": True},
    )
    result = await api.robot_move_carry("1", "CartPanels-1", "SITE-001-40")
    assert result["success"] is True

    req = httpx_mock.get_request()
    assert json.loads(req.content) == {
        "robotId": "1",
        "containerCode": "CartPanels-1",
        "targetNodeCode": "SITE-001-40",
    }


@pytest.mark.asyncio
async def test_robot_drop_at_node(api, httpx_mock):
    httpx_mock.add_response(
        url=f"{BASE_URL}/interfaces/api/amr/robotDrop",
        json={"success": True},
    )
    result = await api.robot_drop("1", "SITE-001-40")
    assert result["success"] is True

    req = httpx_mock.get_request()
    body = json.loads(req.content)
    assert body == {"robotId": "1", "nodeCode": "SITE-001-40"}


@pytest.mark.asyncio
async def test_robot_drop_in_place(api, httpx_mock):
    """Drop in place — no node code, lower mechanism at current position."""
    httpx_mock.add_response(
        url=f"{BASE_URL}/interfaces/api/amr/robotDrop",
        json={"success": True},
    )
    result = await api.robot_drop("1")
    assert result["success"] is True

    req = httpx_mock.get_request()
    body = json.loads(req.content)
    assert body == {"robotId": "1"}
    assert "nodeCode" not in body


@pytest.mark.asyncio
async def test_charge_robot(api, httpx_mock):
    httpx_mock.add_response(
        url=f"{BASE_URL}/interfaces/api/amr/chargeRobot",
        json={"success": True},
    )
    result = await api.charge_robot("1")
    assert result["success"] is True

    req = httpx_mock.get_request()
    body = json.loads(req.content)
    assert body["robotId"] == "1"
    assert body["necessary"] == 1
    assert body["targetLevel"] == 90
    assert body["lowestLevel"] == 5


# -- Map image -------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_map_image_success(api, httpx_mock):
    image_bytes = b"\x89PNG\r\n\x1a\n"
    httpx_mock.add_response(
        url=f"{BASE_URL}/fileserver/SITE/map.png",
        content=image_bytes,
        status_code=200,
    )
    result = await api.fetch_map_image("/SITE/map.png")
    assert result == image_bytes


@pytest.mark.asyncio
async def test_fetch_map_image_404(api, httpx_mock):
    httpx_mock.add_response(
        url=f"{BASE_URL}/fileserver/SITE/missing.png",
        status_code=404,
    )
    result = await api.fetch_map_image("/SITE/missing.png")
    assert result is None
