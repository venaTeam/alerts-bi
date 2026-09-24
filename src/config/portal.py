"""Configuration for the read-only review portal (design section 7.10).

Separate from :mod:`src.config.api` because the two surfaces must never share a listener or
a credential. The trigger surface of section 7.9 writes and binds to loopback; the portal
only reads, is meant to be reachable from the company network, and connects as its own SQL
login - a member of the ``alerts_bi_reader`` role and nothing else.

The portal shares the SQL host, port and TLS settings with the pipeline, and replaces the
user and password with ``PORTAL_SQL_USER`` and ``PORTAL_SQL_PASSWORD``.
"""

from __future__ import annotations

import dataclasses
import ipaddress
from dataclasses import dataclass

from src.config.app import AppConfig, load_config
from src.config.env import read_int, read_str
from src.config.sql import SqlConfig

__all__ = [
    "DEFAULT_ALLOWED_NETWORKS",
    "DEFAULT_PORTAL_HOST",
    "DEFAULT_PORTAL_PORT",
    "DEFAULT_READER_LOGIN",
    "IpNetwork",
    "PortalSettings",
    "load_portal_settings",
    "parse_networks",
]

IpNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network

DEFAULT_PORTAL_HOST = "127.0.0.1"
DEFAULT_PORTAL_PORT = 8100
DEFAULT_READER_LOGIN = "alerts_bi_portal"

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
    """Where the portal listens, whom it admits, and the read-only credential it uses."""

    #: The reader credential. Never the owning one: the portal refuses to start if it can write.
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
    database: str | None = None,
) -> PortalSettings:
    """Read the portal's settings from the environment, with explicit overrides winning."""
    resolved = config or load_config()
    reader = dataclasses.replace(
        resolved.sql,
        user=read_str("PORTAL_SQL_USER", DEFAULT_READER_LOGIN),
        password=read_str("PORTAL_SQL_PASSWORD"),
    )
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
        sql=reader,
        database=database or read_str("PORTAL_DATABASE") or resolved.sql.database,
        host=host or read_str("PORTAL_HOST", DEFAULT_PORTAL_HOST),
        port=port or read_int("PORTAL_PORT", DEFAULT_PORTAL_PORT),
        allowed_networks=networks,
        page_size=page_size,
    )
