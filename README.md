<!--
SPDX-FileCopyrightText: 2026 InOrbit, Inc.

SPDX-License-Identifier: MIT
-->

# InOrbit KUKA AMR Connector

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)

InOrbit Edge connector for KUKA KMP 600P AMRs via the KUKA.AMR Fleet Interface Manager API.

## Overview

This connector bridges [KUKA.AMR Fleet](https://www.kuka.com/) robots with [InOrbit](https://inorbit.ai/) for monitoring and command execution. It runs **one process per robot** using the [`Connector`](https://github.com/inorbit-ai/inorbit-connector-python) base class, designed for Docker Compose deployments where each robot gets its own container.

Built on top of [`inorbit-connector-python`](https://github.com/inorbit-ai/inorbit-connector-python).

## Features

- Real-time robot telemetry (pose, battery, state, velocity, operational mode)
- Navigation commands via InOrbit (NAV_GOAL mapped to nearest KUKA graph node)
- Map image synchronization from KUKA fileserver to InOrbit
- Custom YAML fleet config with common+per-robot merging and `${ENV_VAR}` expansion
- Multi-stage Docker build with [uv](https://github.com/astral-sh/uv) for fast, reproducible builds

## Requirements

- Python 3.12 or later
- InOrbit account [(free to sign up)](https://control.inorbit.ai/)
- Network access to a KUKA.AMR Fleet Interface Manager (HTTP API on port 5000)

## Setup

1. Install dependencies:

```shell
# Using uv (recommended)
uv sync

# Or with pip
pip install -e ".[dev]"
```

2. Configure the connector:

- Copy `config/fleet.example.yaml` to `config/my_fleet.yaml` and fill in your fleet details. Each robot needs a section with `kuka_robot_id` matching the KUKA Fleet robot ID.

- Copy `config/example.env` to `config/.env` and set your credentials:
  - `INORBIT_API_KEY` — from [InOrbit Developer Console](https://developer.inorbit.ai/)
  - `KUKA_INTERFACE_USERNAME` / `KUKA_INTERFACE_PASSWORD` — Interface Manager credentials

## Running

```bash
# Single robot
source config/.env && inorbit-kuka-connector -c config/my_fleet.yaml -id kuka-1
```

### Docker

The connector is designed to run as one container per robot via Docker Compose:

1. Copy `config/example.env` to `.env` and fill in your credentials
2. Copy `config/fleet.example.yaml` to `config/my_fleet.yaml`
3. Run: `docker compose up --build`

See `docker-compose.yaml` for the single-robot dev setup, or the deployment repo's `docker-compose.yaml` for multi-robot production configuration.

## Development

```bash
# Install dev dependencies
uv sync --extra dev

# Run tests
pytest tests/ -v

# Lint
ruff check
ruff format --check

# Run full CI suite locally (lint + tests + coverage)
uv run tox
```

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.
