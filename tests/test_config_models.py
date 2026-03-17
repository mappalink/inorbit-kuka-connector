# SPDX-FileCopyrightText: 2026 Mappalink
#
# SPDX-License-Identifier: MIT

"""Tests for inorbit_kuka_connector.src.config.models."""

from __future__ import annotations

import pytest

from inorbit_kuka_connector.src.config.models import KukaConnectorConfig


REQUIRED_KUKA_FIELDS = {
    "fleet_url": "http://192.168.1.100:5000",
    "username": "admin",
    "password": "secret",
    "kuka_robot_id": "1",
    "robot_model": "KMP 600P-EU-DIC diffDrive",
}


def test_valid_kuka_config():
    """All required fields present — should succeed."""
    cfg = KukaConnectorConfig(**REQUIRED_KUKA_FIELDS)
    assert cfg.fleet_url == "http://192.168.1.100:5000"
    assert cfg.kuka_robot_id == "1"


def test_defaults():
    """Optional fields use sensible defaults."""
    cfg = KukaConnectorConfig(**REQUIRED_KUKA_FIELDS)
    assert cfg.poll_frequency == 1.0
    assert cfg.map_image_path is None
    assert cfg.map_resolution == 0.05
    assert cfg.nodes_file is None
    assert cfg.node_margin_cm == 5


def test_missing_required_field():
    """Missing required field raises ValidationError."""
    incomplete = {**REQUIRED_KUKA_FIELDS}
    del incomplete["fleet_url"]
    with pytest.raises(Exception):
        KukaConnectorConfig(**incomplete)


def test_override_defaults():
    """Explicit values override defaults."""
    cfg = KukaConnectorConfig(
        **REQUIRED_KUKA_FIELDS,
        poll_frequency=2.0,
        map_resolution=0.1,
        node_margin_cm=10,
    )
    assert cfg.poll_frequency == 2.0
    assert cfg.map_resolution == 0.1
    assert cfg.node_margin_cm == 10


def test_env_var_fallback(monkeypatch):
    """KukaConnectorConfig reads from INORBIT_KUKA_ env vars."""
    monkeypatch.setenv("INORBIT_KUKA_FLEET_URL", "http://env-host:5000")
    monkeypatch.setenv("INORBIT_KUKA_USERNAME", "env-user")
    monkeypatch.setenv("INORBIT_KUKA_PASSWORD", "env-pass")
    monkeypatch.setenv("INORBIT_KUKA_KUKA_ROBOT_ID", "99")
    monkeypatch.setenv("INORBIT_KUKA_ROBOT_MODEL", "KMP 600P")

    cfg = KukaConnectorConfig(
        fleet_url="http://env-host:5000",
        username="env-user",
        password="env-pass",
        kuka_robot_id="99",
        robot_model="KMP 600P",
    )
    assert cfg.fleet_url == "http://env-host:5000"
    assert cfg.kuka_robot_id == "99"
