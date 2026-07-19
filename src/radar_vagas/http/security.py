from __future__ import annotations

import ipaddress
import socket
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

from radar_vagas.http.errors import DnsResolutionError, ForbiddenAddressError, InvalidUrlError


class DNSResolver(Protocol):
    def resolve(self, hostname: str) -> Sequence[str]:
        """Resolve a hostname to textual IP addresses."""


@dataclass(frozen=True)
class SystemDNSResolver:
    def resolve(self, hostname: str) -> Sequence[str]:
        try:
            infos = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise DnsResolutionError(f"Falha ao resolver DNS para {hostname}.") from exc
        addresses = sorted({str(info[4][0]) for info in infos})
        if not addresses:
            raise DnsResolutionError(f"DNS nao retornou enderecos para {hostname}.")
        return addresses


@dataclass(frozen=True)
class UrlPolicy:
    allowed_ports: tuple[int, ...] = (80, 443)
    resolver: DNSResolver = SystemDNSResolver()

    def validate_url(self, url: str) -> str:
        parts = urlsplit(url)
        if parts.scheme.lower() not in {"http", "https"}:
            raise InvalidUrlError("Somente URLs http e https sao permitidas.")
        if not parts.hostname:
            raise InvalidUrlError("URL sem host.")
        if parts.username or parts.password:
            raise InvalidUrlError("URL com credenciais embutidas nao e permitida.")

        try:
            port = parts.port
        except ValueError as exc:
            raise InvalidUrlError("Porta invalida na URL.") from exc
        if port is not None and port not in self.allowed_ports:
            raise InvalidUrlError(f"Porta nao permitida: {port}.")

        hostname = _normalize_hostname(parts.hostname)
        if hostname == "localhost" or hostname.endswith(".local"):
            raise ForbiddenAddressError("Host local nao e permitido.")

        addresses = _literal_or_resolved_addresses(hostname, self.resolver)
        for address in addresses:
            _validate_ip_address(address)
        return url


def _normalize_hostname(hostname: str) -> str:
    normalized = hostname.strip().strip(".").lower()
    if not normalized:
        raise InvalidUrlError("URL sem host.")
    if "%" in normalized:
        raise InvalidUrlError("Zona IPv6 nao e permitida na URL.")
    return normalized


def _literal_or_resolved_addresses(hostname: str, resolver: DNSResolver) -> Sequence[str]:
    try:
        ipaddress.ip_address(hostname)
        return [hostname]
    except ValueError:
        return resolver.resolve(hostname)


def _validate_ip_address(address: str) -> None:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError as exc:
        raise DnsResolutionError(f"Endereco DNS invalido: {address}.") from exc
    if not ip.is_global:
        raise ForbiddenAddressError(f"Endereco bloqueado pela politica SSRF: {address}.")
