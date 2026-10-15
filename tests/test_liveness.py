# SPDX-FileCopyrightText: 2026 Mappalink
#
# SPDX-License-Identifier: MIT

"""Tests for robot liveness: a KUKA that the Fleet Manager reports as Offline
(status 2), or that cannot be queried at all for a while, is reported offline
to InOrbit instead of staying online with its last known values.

The edge SDK publishes a retained "online" status when the session connects
and InOrbit only asks ``_is_robot_online`` (get_state) once it already believes
the robot is offline, so the connector must push the change itself.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from inorbit_kuka_connector.src.config.models import KukaConnectorConfig
from inorbit_kuka_connector.src.connector import KukaAmrConnector


@pytest.fixture()
def clock(monkeypatch):
    now = {"t": 1000.0}
    monkeypatch.setattr(time, "monotonic", lambda: now["t"])
    return now


def _robot(status: int) -> dict:
    return {"status": status, "x": "1000", "y": "2000", "robotOrientation": "90", "mapCode": "M"}


def _connector(status: int | Exception) -> KukaAmrConnector:
    """A connector stub, bypassing __init__, whose Fleet Manager answers ``status``."""
    conn = object.__new__(KukaAmrConnector)
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


def test_liveness_timeout_defaults_to_a_minute() -> None:
    assert _cfg().liveness_timeout == 60.0


def test_liveness_timeout_must_be_positive() -> None:
    with pytest.raises(ValueError):
        _cfg(liveness_timeout=0)
