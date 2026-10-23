# SPDX-FileCopyrightText: 2026 Mappalink
#
# SPDX-License-Identifier: MIT

"""Tests for robot liveness. A KUKA that cannot be queried at all for a while
is reported offline to InOrbit. A KUKA that the Fleet Manager reports as Offline
(status 2) is reported offline too, and its pose is no longer published:
InOrbit zones count a pose that keeps arriving, offline or not. With
fleet_offline_visible true it stays online at the Fleet Manager's pose instead
(visible on the map, holding zones) and refuses motion.

The edge SDK publishes a retained "online" status when the session connects
and InOrbit only asks ``_is_robot_online`` (get_state) once it already believes
the robot is offline, so the connector must push the change itself.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from inorbit_connector.connector import CommandResultCode

from inorbit_kuka_connector.src.config.models import KukaConnectorConfig
from inorbit_kuka_connector.src.connector import KukaAmrConnector


@pytest.fixture()
def clock(monkeypatch):
    now = {"t": 1000.0}
    monkeypatch.setattr(time, "monotonic", lambda: now["t"])
    return now


def _robot(status: int) -> dict:
    return {"status": status, "x": "1000", "y": "2000", "robotOrientation": "90", "mapCode": "M"}


def _connector(status: int | Exception, fleet_offline_visible: bool = False) -> KukaAmrConnector:
    """A connector stub, bypassing __init__, whose Fleet Manager answers ``status``."""
    conn = object.__new__(KukaAmrConnector)
    conn._fleet_offline_visible = fleet_offline_visible
    conn._fleet_offline = False
    conn._kuka_robot_id = "1"
    conn._current_kuka_mission_code = None
    conn._mission_code_submit_time = 0.0
    conn._liveness_timeout = 60.0
    conn._last_fleet_reply_at = None
    conn._robot_online = None  # unknown until the Fleet Manager answers
    conn._reported_online = None
    conn._offline_reasserted_at = None
    conn._api = MagicMock()
    _answer(conn, status)
    conn._poll_active_job = AsyncMock(return_value=None)
    conn.publish_pose = MagicMock()
    conn.publish_key_values = MagicMock()
    conn._session = MagicMock()
    conn._get_session = MagicMock(return_value=conn._session)
    return conn


def _answer(conn: KukaAmrConnector, status: int | Exception) -> None:
    if isinstance(status, Exception):
        conn._api.robot_query = AsyncMock(side_effect=status)
    else:
        conn._api.robot_query = AsyncMock(return_value={"success": True, "data": [_robot(status)]})


def _tick(conn: KukaAmrConnector) -> None:
    asyncio.run(conn._execution_loop())


def _status_calls(conn) -> list[bool]:
    return [c.kwargs["online"] for c in conn._session._send_robot_status.call_args_list]


def test_idle_robot_is_online_and_needs_no_status_push(clock) -> None:
    conn = _connector(3)
    _tick(conn)
    _tick(conn)
    assert conn._is_robot_online() is True
    assert _status_calls(conn) == []


def test_fleet_manager_offline_status_is_reported_offline_once(clock) -> None:
    conn = _connector(2)
    _tick(conn)
    _tick(conn)
    assert conn._is_robot_online() is False
    assert _status_calls(conn) == [False]


def test_robot_coming_back_is_reported_online(clock) -> None:
    conn = _connector(2)
    _tick(conn)
    _answer(conn, 3)
    _tick(conn)
    assert conn._is_robot_online() is True
    assert _status_calls(conn) == [False, True]


def test_offline_is_reasserted_periodically(clock) -> None:
    conn = _connector(2)
    _tick(conn)
    clock["t"] += 10.0
    _tick(conn)
    assert _status_calls(conn) == [False]
    clock["t"] += 25.0
    _tick(conn)
    assert _status_calls(conn) == [False, False]


def test_short_fleet_manager_outage_keeps_robot_online(clock) -> None:
    conn = _connector(3)
    _tick(conn)
    _answer(conn, ConnectionError("fleet manager down"))
    clock["t"] += 30.0
    _tick(conn)
    assert conn._is_robot_online() is True
    assert _status_calls(conn) == []


def test_long_fleet_manager_outage_is_reported_offline(clock) -> None:
    conn = _connector(3)
    _tick(conn)
    _answer(conn, ConnectionError("fleet manager down"))
    clock["t"] += 61.0
    _tick(conn)
    assert conn._is_robot_online() is False
    assert _status_calls(conn) == [False]


def test_never_answered_fleet_manager_is_offline_after_timeout(clock) -> None:
    conn = _connector(ConnectionError("fleet manager down"))
    _tick(conn)
    assert _status_calls(conn) == []  # unknown yet, give it the timeout
    clock["t"] += 61.0
    _tick(conn)
    assert conn._is_robot_online() is False
    assert _status_calls(conn) == [False]


def test_offline_robot_publishes_no_pose(clock) -> None:
    # The Fleet Manager keeps reporting the last pose of an Offline robot; a
    # pose that keeps arriving holds InOrbit zones, so it is not published.
    conn = _connector(2)
    _tick(conn)
    conn.publish_pose.assert_not_called()


def test_offline_robot_still_publishes_fleet_manager_values(clock) -> None:
    # The Fleet Manager's own view (status Offline, last pose, battery) is real
    # data and drives the Error mode in InOrbit; keep publishing it.
    conn = _connector(2)
    _tick(conn)
    conn.publish_key_values.assert_called_once()
    assert conn.publish_key_values.call_args.kwargs["robot_status_text"] == "Offline"


def _cfg(**kw) -> KukaConnectorConfig:
    base = dict(
        fleet_url="http://fm", username="u", password="p", kuka_robot_id="1", robot_model="KMP"
    )
    return KukaConnectorConfig(**base, **kw)


# -- fleet_offline_visible (the default) ---------------------------------------


def test_visible_fleet_offline_robot_stays_online(clock) -> None:
    conn = _connector(2, fleet_offline_visible=True)
    _tick(conn)
    clock["t"] += 61.0
    _tick(conn)
    assert conn._is_robot_online() is True
    assert _status_calls(conn) == []
    conn.publish_pose.assert_called()
    assert conn.publish_key_values.call_args.kwargs["robot_status_text"] == "Offline"


def test_visible_fleet_offline_robot_after_reported_offline_comes_back_online(clock) -> None:
    # A robot reported offline earlier (e.g. by a Fleet Manager outage) is
    # reported online again once the Fleet Manager answers, even with Offline.
    conn = _connector(ConnectionError("fleet manager down"), fleet_offline_visible=True)
    _tick(conn)
    clock["t"] += 61.0
    _tick(conn)
    _answer(conn, 2)
    _tick(conn)
    assert _status_calls(conn) == [False, True]


def test_visible_robot_still_goes_offline_when_fleet_manager_is_silent(clock) -> None:
    conn = _connector(2, fleet_offline_visible=True)
    _tick(conn)
    _answer(conn, ConnectionError("fleet manager down"))
    clock["t"] += 61.0
    _tick(conn)
    assert conn._is_robot_online() is False
    assert _status_calls(conn) == [False]


def _command(conn, command_name, args):
    from inorbit_kuka_connector.src import connector as mod

    result_fn = MagicMock()
    conn._mission_executor = MagicMock()
    conn._mission_executor.handle_command = AsyncMock(return_value=False)
    conn._handle_custom_command = AsyncMock()
    conn._handle_nav_goal = AsyncMock()
    asyncio.run(
        conn._inorbit_command_handler(
            getattr(mod, command_name), args, {"result_function": result_fn}
        )
    )
    return result_fn


def _custom(script_name: str) -> list:
    return [script_name, []]


@pytest.mark.parametrize(
    "command_name,args",
    [
        ("COMMAND_NAV_GOAL", [{"x": "1", "y": "2", "theta": "0"}]),
        ("COMMAND_CUSTOM_COMMAND", _custom("move_to_node")),
        ("COMMAND_CUSTOM_COMMAND", _custom("charge")),
        ("COMMAND_CUSTOM_COMMAND", _custom("lift")),
        ("COMMAND_CUSTOM_COMMAND", _custom("executeMissionAction")),
    ],
)
def test_fleet_offline_robot_refuses_motion(clock, command_name, args) -> None:
    conn = _connector(2, fleet_offline_visible=True)
    _tick(conn)
    result_fn = _command(conn, command_name, args)
    assert result_fn.call_args.args[0] == CommandResultCode.FAILURE
    assert "Offline" in result_fn.call_args.kwargs["execution_status_details"]
    conn._handle_nav_goal.assert_not_called()
    conn._handle_custom_command.assert_not_called()
    conn._mission_executor.handle_command.assert_not_called()


@pytest.mark.parametrize("script", ["cancel_mission", "abort_missions", "cancelMissionAction"])
def test_fleet_offline_robot_still_takes_cancel(clock, script) -> None:
    conn = _connector(2, fleet_offline_visible=True)
    _tick(conn)
    _command(conn, "COMMAND_CUSTOM_COMMAND", _custom(script))
    conn._mission_executor.handle_command.assert_called_once()


def test_online_robot_takes_motion(clock) -> None:
    conn = _connector(3, fleet_offline_visible=True)
    _tick(conn)
    _command(conn, "COMMAND_NAV_GOAL", [{"x": "1", "y": "2", "theta": "0"}])
    conn._handle_nav_goal.assert_called_once()


def test_fleet_offline_visible_defaults_to_false() -> None:
    assert _cfg().fleet_offline_visible is False


def test_liveness_timeout_defaults_to_a_minute() -> None:
    assert _cfg().liveness_timeout == 60.0


def test_liveness_timeout_must_be_positive() -> None:
    with pytest.raises(ValueError):
        _cfg(liveness_timeout=0)
