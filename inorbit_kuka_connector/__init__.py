# SPDX-FileCopyrightText: 2026 Mappalink
#
# SPDX-License-Identifier: MIT

"""Top-level package for InOrbit KUKA AMR Connector."""

from importlib import metadata

__author__ = """InOrbit Inc."""
__email__ = "support@inorbit.ai"
# Read the installed package version from metadata
try:
    __version__ = metadata.version("inorbit-kuka-connector")
except metadata.PackageNotFoundError:
    __version__ = "unknown"
