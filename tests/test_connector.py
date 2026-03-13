# SPDX-FileCopyrightText: 2026 Mappalink
#
# SPDX-License-Identifier: MIT

"""Tests for pure helper functions in inorbit_kuka_connector.src.connector."""

from __future__ import annotations

import json
import math

import pytest

from inorbit_connector.connector import CommandResultCode

from inorbit_kuka_connector.src.connector import (
    JOB_STATUS,
    KukaAmrConnector,
    _ACTIVE_JOB_STATUSES,
)


class TestScriptArgsParsing:
    """args[1] from COMMAND_CUSTOM_COMMAND is a list of alternating key-value strings."""

    def test_dict_conversion(self):
        args_list = ["--node_code", "SITE-001-40", "--extra", "foo"]
        script_args = dict(zip(args_list[::2], args_list[1::2]))
        assert script_args["--node_code"] == "SITE-001-40"
        assert script_args["--extra"] == "foo"

    def test_missing_key_raises(self):
        args_list = ["--other", "value"]
        script_args = dict(zip(args_list[::2], args_list[1::2]))
        with pytest.raises(KeyError):
            _ = script_args["--node_code"]

    def test_single_pair(self):
        args_list = ["--mission_code", "MC-001"]
        script_args = dict(zip(args_list[::2], args_list[1::2]))
        assert script_args["--mission_code"] == "MC-001"

    def test_empty_args(self):
        args_list = []
        script_args = dict(zip(args_list[::2], args_list[1::2]))
        assert script_args == {}


class TestFindNearestNode:
    """Test _find_nearest_node as a standalone method (bypassing __init__)."""

    @staticmethod
    def _make_connector_stub(nodes):
        """Create a minimal object with _nodes set, bypassing __init__."""
        obj = object.__new__(KukaAmrConnector)
        obj._nodes = nodes
        return obj

    def test_finds_correct_node(self):
        nodes = [
            ("A", 0.0, 0.0),
            ("B", 10.0, 10.0),
            ("C", 5.0, 5.0),
        ]
        c = self._make_connector_stub(nodes)
        node, dist = c._find_nearest_node(5.1, 4.9)
        assert node == "C"
        assert dist == pytest.approx(math.hypot(0.1, -0.1), abs=1e-6)

    def test_empty_nodes_returns_none(self):
        c = self._make_connector_stub([])
        node, dist = c._find_nearest_node(1.0, 1.0)
        assert node is None
        assert dist == 0.0

    def test_tie_breaking_first_wins(self):
        nodes = [
            ("A", 0.0, 0.0),
            ("B", 0.0, 0.0),  # same coordinates
        ]
        c = self._make_connector_stub(nodes)
        node, dist = c._find_nearest_node(0.0, 0.0)
        assert node == "A"
        assert dist == 0.0


class TestLoadNodes:
    def test_parses_json(self, tmp_path):
        data = {
            "floorList": [
                {
                    "nodeList": [
                        {"nodeUuid": "N1", "xCoordinate": 1.5, "yCoordinate": 2.5},
                        {"nodeUuid": "N2", "xCoordinate": 3.0, "yCoordinate": 4.0},
                    ]
                }
            ]
        }
        p = tmp_path / "nodes.json"
        p.write_text(json.dumps(data))
        nodes = KukaAmrConnector._load_nodes(str(p))
        assert len(nodes) == 2
        assert nodes[0] == ("N1", 1.5, 2.5)
        assert nodes[1] == ("N2", 3.0, 4.0)


class TestExtractJobKv:
    """Test _extract_job_kv static method."""

    def test_no_job_returns_empty_values(self):
        kv = KukaAmrConnector._extract_job_kv(None)
        assert kv["job_status"] == ""
        assert kv["job_status_text"] == ""
        assert kv["job_target_node"] == ""
        assert kv["job_workflow_name"] == ""
        assert kv["job_warn_code"] == ""
        assert kv["job_create_time"] == ""
        # All 9 keys present
        assert len(kv) == 9

    def test_executing_job(self):
        job = {
            "jobCode": "T001",
            "status": 20,
            "targetCellCode": "SITE-001-90",
            "beginCellCode": "SITE-001-80",
            "finalNodeCode": "SITE-001-90",
            "workflowName": "Carry01",
            "warnCode": None,
            "createTime": "2026-02-19 15:51:14",
            "source": "SELF",
        }
        kv = KukaAmrConnector._extract_job_kv(job)
        assert kv["job_status"] == 20
        assert kv["job_status_text"] == "Executing"
        assert kv["job_target_node"] == "SITE-001-90"
        assert kv["job_begin_node"] == "SITE-001-80"
        assert kv["job_final_node"] == "SITE-001-90"
        assert kv["job_workflow_name"] == "Carry01"
        assert kv["job_create_time"] == "2026-02-19 15:51:14"
        assert kv["job_source"] == "SELF"

    def test_warning_job(self):
        job = {"status": 50, "warnCode": "CHARGING_TIMEOUT", "workflowName": "ManualCharging"}
        kv = KukaAmrConnector._extract_job_kv(job)
        assert kv["job_status"] == 50
        assert kv["job_status_text"] == "Warning"
        assert kv["job_warn_code"] == "CHARGING_TIMEOUT"

    def test_missing_fields_default_to_empty(self):
        job = {"status": 20}
        kv = KukaAmrConnector._extract_job_kv(job)
        assert kv["job_status"] == 20
        assert kv["job_target_node"] == ""
        assert kv["job_workflow_name"] == ""
        assert len(kv) == 9


class TestJobStatusMapping:
    """Verify JOB_STATUS and _ACTIVE_JOB_STATUSES are consistent."""

    def test_active_statuses_are_subset(self):
        assert _ACTIVE_JOB_STATUSES <= set(JOB_STATUS.keys())

    def test_completed_not_active(self):
        for code in (30, 31, 35):
            assert code not in _ACTIVE_JOB_STATUSES


class TestHandleMessage:
    """Test _handle_message (cloud-mode pause/resume)."""

    @staticmethod
    def _make_stub(mission_code, api_response=None, api_raises=None):
        """Create a minimal connector stub with a mock API."""
        from unittest.mock import AsyncMock

        obj = object.__new__(KukaAmrConnector)
        obj._current_kuka_mission_code = mission_code
        obj._api = AsyncMock()
        if api_raises:
            obj._api.pause_mission.side_effect = api_raises
            obj._api.recover_mission.side_effect = api_raises
        else:
            obj._api.pause_mission.return_value = api_response or {"success": True}
            obj._api.recover_mission.return_value = api_response or {"success": True}
        return obj

    @pytest.mark.asyncio
    async def test_pause_calls_api(self):
        c = self._make_stub("MC-001")
        results = []
        await c._handle_message("inorbit_pause", lambda code, **kw: results.append(code))
        c._api.pause_mission.assert_called_once_with("MC-001")
        assert results[0] == CommandResultCode.SUCCESS

    @pytest.mark.asyncio
    async def test_resume_calls_api(self):
        c = self._make_stub("MC-001")
        results = []
        await c._handle_message("inorbit_resume", lambda code, **kw: results.append(code))
        c._api.recover_mission.assert_called_once_with("MC-001")
        assert results[0] == CommandResultCode.SUCCESS

    @pytest.mark.asyncio
    async def test_pause_no_active_mission(self):
        c = self._make_stub(None)
        results = []
        await c._handle_message("inorbit_pause", lambda code, **kw: results.append(code))
        c._api.pause_mission.assert_not_called()
        assert results[0] == CommandResultCode.FAILURE

    @pytest.mark.asyncio
    async def test_resume_no_active_mission(self):
        c = self._make_stub(None)
        results = []
        await c._handle_message("inorbit_resume", lambda code, **kw: results.append(code))
        c._api.recover_mission.assert_not_called()
        assert results[0] == CommandResultCode.FAILURE

    @pytest.mark.asyncio
    async def test_api_error_returns_failure(self):
        c = self._make_stub("MC-001", api_raises=RuntimeError("timeout"))
        results = []
        await c._handle_message("inorbit_pause", lambda code, **kw: results.append(code))
        assert results[0] == CommandResultCode.FAILURE

    @pytest.mark.asyncio
    async def test_unknown_message_returns_failure(self):
        c = self._make_stub("MC-001")
        results = []
        await c._handle_message("something_else", lambda code, **kw: results.append(code))
        assert results[0] == CommandResultCode.FAILURE

    @pytest.mark.asyncio
    async def test_api_returns_failure(self):
        c = self._make_stub("MC-001", api_response={"success": False, "msg": "err"})
        results = []
        await c._handle_message("inorbit_pause", lambda code, **kw: results.append(code))
        assert results[0] == CommandResultCode.FAILURE


class TestPauseResumeRobot:
    """Test pauseRobot / resumeRobot custom commands (FlowCore traffic management)."""

    @staticmethod
    def _make_stub(mission_code, api_response=None):
        from unittest.mock import AsyncMock

        obj = object.__new__(KukaAmrConnector)
        obj._current_kuka_mission_code = mission_code
        obj._kuka_robot_id = "1"
        obj._api = AsyncMock()
        obj._api.pause_mission.return_value = api_response or {"success": True}
        obj._api.recover_mission.return_value = api_response or {"success": True}
        return obj

    @pytest.mark.asyncio
    async def test_pause_robot_calls_api(self):
        c = self._make_stub("MC-001")
        results = []
        await c._handle_custom_command("pauseRobot", {}, lambda code, **kw: results.append(code))
        c._api.pause_mission.assert_called_once_with("MC-001")
        assert results[0] == CommandResultCode.SUCCESS

    @pytest.mark.asyncio
    async def test_resume_robot_calls_api(self):
        c = self._make_stub("MC-001")
        results = []
        await c._handle_custom_command("resumeRobot", {}, lambda code, **kw: results.append(code))
        c._api.recover_mission.assert_called_once_with("MC-001")
        assert results[0] == CommandResultCode.SUCCESS

    @pytest.mark.asyncio
    async def test_pause_robot_no_mission_succeeds(self):
        """pauseRobot with no active mission returns SUCCESS (robot is already stopped)."""
        c = self._make_stub(None)
        results = []
        await c._handle_custom_command("pauseRobot", {}, lambda code, **kw: results.append(code))
        c._api.pause_mission.assert_not_called()
        assert results[0] == CommandResultCode.SUCCESS

    @pytest.mark.asyncio
    async def test_resume_robot_no_mission_succeeds(self):
        c = self._make_stub(None)
        results = []
        await c._handle_custom_command("resumeRobot", {}, lambda code, **kw: results.append(code))
        c._api.recover_mission.assert_not_called()
        assert results[0] == CommandResultCode.SUCCESS


class TestReportResult:
    def test_success(self):
        results = []
        KukaAmrConnector._report_result({"success": True}, lambda code, **kw: results.append(code))
        assert len(results) == 1

    def test_failure(self):
        results = []
        KukaAmrConnector._report_result(
            {"success": False, "msg": "err"},
            lambda code, **kw: results.append(code),
        )
        assert len(results) == 1
