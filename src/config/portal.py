"""Configuration for the review portal (design section 7.10).

The portal has its own listener and client allowlist, but uses the pipeline's exact SQL
connection settings, including its database and login.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass

from src.config.app import AppConfig, load_config
from src.config.env import read_int, read_str
from src.config.sql import SqlConfig

__all__ = [
    "DEFAULT_ALLOWED_NETWORKS",
    "DEFAULT_PORTAL_HOST",
    "DEFAULT_PORTAL_PORT",
    "IpNetwork",
    "PortalSettings",
    "load_portal_settings",
    "parse_networks",
]

IpNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network

DEFAULT_PORTAL_HOST = "127.0.0.1"
DEFAULT_PORTAL_PORT = 8100

#: Loopback plus the private address ranges: reachable from a company network, refused from
#: anywhere else. Behind a reverse proxy the client address is the proxy's, so narrow this to
#: the proxy there.
DEFAULT_ALLOWED_NETWORKS = (
    "127.0.0.0/8",
    "::1/128",
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "fc00::/7",
)


def parse_networks(values: tuple[str, ...]) -> tuple[IpNetwork, ...]:
    """Parse an allowlist, refusing an empty one and any entry that is not a network."""
    if not values:
        raise ValueError("PORTAL_ALLOWED_NETWORKS is empty; the portal would refuse everyone")
    networks: list[IpNetwork] = []
    for value in values:
        try:
            networks.append(ipaddress.ip_network(value, strict=False))
        except ValueError as exc:
            raise ValueError(f"PORTAL_ALLOWED_NETWORKS entry {value!r} is not a network") from exc
    return tuple(networks)


@dataclass(frozen=True, slots=True)
class PortalSettings:
    """Where the portal listens, whom it admits, and its SQL connection."""

    sql: SqlConfig
    database: str
    host: str = DEFAULT_PORTAL_HOST
    port: int = DEFAULT_PORTAL_PORT
    allowed_networks: tuple[str, ...] = DEFAULT_ALLOWED_NETWORKS
    page_size: int = 25

    def networks(self) -> tuple[IpNetwork, ...]:
        return parse_networks(self.allowed_networks)


def load_portal_settings(
    config: AppConfig | None = None,
    host: str | None = None,
    port: int | None = None,
) -> PortalSettings:
    """Read portal settings while reusing the application's SQL connection exactly."""
    resolved = config or load_config()
    networks = tuple(
        part.strip()
        for part in read_str("PORTAL_ALLOWED_NETWORKS", ",".join(DEFAULT_ALLOWED_NETWORKS)).split(
            ","
        )
        if part.strip()
    )
    parse_networks(networks)
    page_size = read_int("PORTAL_PAGE_SIZE", 25)
    if not 1 <= page_size <= 200:
        raise ValueError(f"PORTAL_PAGE_SIZE must be between 1 and 200, got {page_size}")
    return PortalSettings(
        sql=resolved.sql,
        database=resolved.sql.database,
        host=host or read_str("PORTAL_HOST", DEFAULT_PORTAL_HOST),
        port=port or read_int("PORTAL_PORT", DEFAULT_PORTAL_PORT),
        allowed_networks=networks,
        page_size=page_size,
    )
