<!--
SPDX-FileCopyrightText: 2026 InOrbit, Inc.

SPDX-License-Identifier: MIT
-->

# InOrbit KUKA AMR Connector

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)

InOrbit Edge connector for KUKA KMP 600P AMRs via the KUKA.AMR Fleet Interface Manager API.

## Overview

This repository contains the [InOrbit](https://inorbit.ai/) Edge Connector for [KUKA](https://www.kuka.com/) KMP 600P robots. It runs **one process per robot** and communicates with the KUKA.AMR Fleet Interface Manager REST API for telemetry and command execution.

Built on top of [`inorbit-connector-python`](https://github.com/inorbit-ai/inorbit-connector-python).

## API Documentation

This connector implementation is based on the KUKA.AMR Fleet Interface Manager Standard Interface API. For detailed API documentation, refer to:

- KUKA.AMR Fleet API manual (provided by KUKA with your Fleet Manager installation)

## Features

- Real-time robot telemetry (pose, battery, status, motor temperatures, lift state, mission codes)
- Custom commands: `move_to_node`, `move_carry`, `lift`, `drop`, `charge`, `unlock`
- Mission control: `pause_mission`, `resume_mission`, `cancel_mission`
- Edge mission execution with SQLite persistence (via [`inorbit-edge-executor`](https://github.com/inorbit-ai/inorbit-edge-executor))
- NAV_GOAL navigation (click-on-map → nearest KUKA graph node resolution)
- Map image synchronization from KUKA fileserver to InOrbit
- Custom YAML fleet config with common+per-robot merging and `${ENV_VAR}` expansion
- Multi-stage Docker build with [uv](https://github.com/astral-sh/uv) for fast, reproducible builds

## Requirements

- Python 3.12 or later
- InOrbit account [(it's free to sign up!)](https://control.inorbit.ai/)
- Network access to a KUKA.AMR Fleet Interface Manager (HTTP API on port 5000)

## Setup

1. Create a Python virtual environment and install the connector:

```shell
# Using uv (recommended)
uv sync
```

> [!TIP]
> Installing the `colorlog` package is optional. If available, it will be used to colorize the logs.

```shell
uv pip install colorlog
```

2. Configure the connector:

- Copy `config/fleet.example.yaml` to `config/my_fleet.yaml` and configure your robot fleet. Each robot needs a section with `kuka_robot_id` matching the KUKA Fleet robot ID.

- Copy `config/example.env` to `config/.env` and set your credentials:
  - `INORBIT_API_KEY` — from [InOrbit Developer Console](https://developer.inorbit.ai/)
  - `KUKA_INTERFACE_USERNAME` / `KUKA_INTERFACE_PASSWORD` — Interface Manager credentials

- Optionally, place a KUKA map graph export (`kuka_nodes.json`) in `config/` for NAV_GOAL nearest-node resolution.

## Deployment

Once all dependencies are installed and the configuration is complete, the connector can be run as a command:

```bash
source config/.env && inorbit-kuka-connector -c config/my_fleet.yaml -id kuka-1
```

### Docker

The connector is designed to run as one container per robot via Docker Compose:

1. Copy `docker/docker-compose.example.yaml` to `docker/docker-compose.yaml`
2. Copy `config/example.env` to `.env` and fill in your credentials
3. Update volume paths in `docker-compose.yaml` to point to your configuration files
4. Run: `docker compose -f docker/docker-compose.yaml up -d`

See `docker/docker-compose.example.yaml` for a multi-robot production setup, or the root `docker-compose.yaml` for a single-robot dev configuration.

## Contributing

Any contribution that you make to this repository will be under the MIT license, as dictated by that [license](https://opensource.org/licenses/MIT).

Please refer to the [CONTRIBUTING.md](CONTRIBUTING.md) file for information on how to contribute to this project.

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.

## Support

- **Documentation**: [InOrbit Developer Docs](https://developer.inorbit.ai/)
- **Issues**: [GitHub Issues](https://github.com/mappalink/inorbit-kuka-connector/issues)
- **Email**: info@mappalink.com

---

**Powered by InOrbit** | [www.inorbit.ai](https://www.inorbit.ai)
