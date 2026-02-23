# SPDX-FileCopyrightText: 2026 InOrbit, Inc.
#
# SPDX-License-Identifier: MIT

"""Entrypoint for the KUKA AMR InOrbit connector."""

import argparse
import logging
import signal
import sys

from inorbit_kuka_connector.src.connector import KukaAmrConnector
from inorbit_kuka_connector.src.config.models import ConnectorConfig
from inorbit_kuka_connector.src.config.fleet_config_loader import get_robot_config

logger = logging.getLogger(__name__)


def start():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    parser = argparse.ArgumentParser(prog="inorbit-kuka-connector")
    parser.add_argument("-c", "--config", required=True, help="Fleet YAML path")
    parser.add_argument("-id", "--robot_id", required=True, help="Robot ID from YAML")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log level",
    )
    args = parser.parse_args()

    logging.getLogger().setLevel(getattr(logging, args.log_level))

    try:
        config = ConnectorConfig(**get_robot_config(args.config, args.robot_id))
    except Exception as e:
        logger.error("Config error: %s", e)
        sys.exit(1)

    connector = KukaAmrConnector(args.robot_id, config)
    logger.info("Starting KUKA connector for %s", args.robot_id)
    connector.start()
    signal.signal(signal.SIGINT, lambda s, f: connector.stop())
    signal.signal(signal.SIGTERM, lambda s, f: connector.stop())
    connector.join()
