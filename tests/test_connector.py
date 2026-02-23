# SPDX-FileCopyrightText: 2026 InOrbit, Inc.
#
# SPDX-License-Identifier: MIT

"""Tests for pure helper functions in inorbit_kuka_connector.src.connector."""

from __future__ import annotations

import json
import math

import pytest

from inorbit_kuka_connector.src.connector import KukaAmrConnector


class TestParseArg:
    def test_extracts_value(self):
        result = KukaAmrConnector._parse_arg("--node_code SITE-001-40 --extra foo", "--node_code")
        assert result == "SITE-001-40"

    def test_raises_on_missing_arg(self):
        with pytest.raises(ValueError, match="Missing argument"):
            KukaAmrConnector._parse_arg("--other value", "--node_code")

    def test_extracts_last_arg(self):
        result = KukaAmrConnector._parse_arg("--mission_code MC-001", "--mission_code")
        assert result == "MC-001"


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
