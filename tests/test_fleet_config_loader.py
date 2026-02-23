# SPDX-FileCopyrightText: 2026 InOrbit, Inc.
#
# SPDX-License-Identifier: MIT

"""Tests for inorbit_kuka_connector.src.config.fleet_config_loader."""

from __future__ import annotations

import textwrap

import pytest

from inorbit_kuka_connector.src.config.fleet_config_loader import (
    get_robot_config,
    _expand_env_vars,
)


FLEET_YAML = textwrap.dedent("""\
    common:
      location_tz: Europe/Amsterdam
      connector_type: KukaAMR
      fleet_url: "http://10.200.30.14:5000"
      username: admin
      password: secret

    kuka-1:
      robot_name: "KUKA #1"
      kuka_robot_id: "1"

    kuka-2:
      robot_name: "KUKA #2"
      kuka_robot_id: "2"
      fleet_url: "http://override:5000"
""")


@pytest.fixture()
def fleet_yaml(tmp_path):
    p = tmp_path / "fleet.yaml"
    p.write_text(FLEET_YAML)
    return str(p)


def test_common_plus_robot_merge(fleet_yaml):
    """Common fields are inherited, robot-specific fields added."""
    cfg = get_robot_config(fleet_yaml, "kuka-1")
    assert cfg["connector_config"]["fleet_url"] == "http://10.200.30.14:5000"
    assert cfg["connector_config"]["kuka_robot_id"] == "1"
    assert cfg["robot_name"] == "KUKA #1"
    assert cfg["location_tz"] == "Europe/Amsterdam"


def test_robot_overrides_common(fleet_yaml):
    """Per-robot values override common values."""
    cfg = get_robot_config(fleet_yaml, "kuka-2")
    assert cfg["connector_config"]["fleet_url"] == "http://override:5000"


def test_kuka_fields_nested_under_connector_config(fleet_yaml):
    """KUKA-specific fields are moved into connector_config."""
    cfg = get_robot_config(fleet_yaml, "kuka-1")
    assert "fleet_url" not in cfg
    assert "username" not in cfg
    assert "password" not in cfg
    assert "kuka_robot_id" not in cfg
    assert cfg["connector_config"]["username"] == "admin"


def test_missing_robot_raises(fleet_yaml):
    """Requesting a non-existent robot ID raises IndexError."""
    with pytest.raises(IndexError, match="not found"):
        get_robot_config(fleet_yaml, "does-not-exist")


def test_env_var_expansion(tmp_path, monkeypatch):
    """${ENV_VAR} references in YAML values are expanded."""
    monkeypatch.setenv("TEST_KUKA_PASSWORD", "expanded_secret")
    p = tmp_path / "fleet.yaml"
    p.write_text(
        textwrap.dedent("""\
        common:
          fleet_url: "http://host:5000"
          username: admin
          password: "${TEST_KUKA_PASSWORD}"
        robot-1:
          kuka_robot_id: "1"
    """)
    )

    cfg = get_robot_config(str(p), "robot-1")
    assert cfg["connector_config"]["password"] == "expanded_secret"


def test_expand_env_vars_recursion(monkeypatch):
    """_expand_env_vars handles nested dicts, lists, and non-strings."""
    monkeypatch.setenv("MY_VAR", "hello")
    result = _expand_env_vars(
        {
            "a": "${MY_VAR}",
            "b": ["${MY_VAR}", 42],
            "c": {"nested": "${MY_VAR}"},
            "d": 3.14,
        }
    )
    assert result == {
        "a": "hello",
        "b": ["hello", 42],
        "c": {"nested": "hello"},
        "d": 3.14,
    }
