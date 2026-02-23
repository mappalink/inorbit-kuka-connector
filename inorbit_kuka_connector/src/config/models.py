# SPDX-FileCopyrightText: 2026 InOrbit, Inc.
#
# SPDX-License-Identifier: MIT

"""Configuration models for the KUKA AMR connector."""

from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict
from inorbit_connector.models import InorbitConnectorConfig


class KukaConnectorConfig(BaseSettings):
    """KUKA AMR Fleet-specific configuration."""

    model_config = SettingsConfigDict(
        env_prefix="INORBIT_KUKA_",
        env_ignore_empty=True,
        extra="allow",
    )

    fleet_url: str  # e.g. "http://192.168.1.100:5000"
    username: str  # Interface Manager username
    password: str  # Interface Manager password (plain text)
    kuka_robot_id: str  # Robot ID in KUKA Fleet (e.g. "1", "2", "100")
    poll_frequency: float = 1.0  # Hz

    # Map image path on the KUKA fileserver (no auth required)
    map_image_path: Optional[str] = None
    map_resolution: float = 0.05  # meters per pixel

    # KUKA graph export JSON (for NAV_GOAL -> nearest node lookup)
    nodes_file: Optional[str] = None
    node_margin_cm: float = 5  # max distance (cm) to accept a node match


class ConnectorConfig(InorbitConnectorConfig):
    """Full config: InOrbit base + KUKA specifics."""

    connector_config: KukaConnectorConfig
