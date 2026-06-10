"""HTTP safety helpers for remote fetches in grading workflows."""

import ipaddress
import os
import socket
from urllib.parse import urlparse

import requests


DEFAULT_MAX_ARTIFACT_BYTES = 25 * 1024 * 1024
DEFAULT_MAX_PREVIEW_BYTES = 1 * 1024 * 1024
DEFAULT_CONNECT_TIMEOUT = 5
DEFAULT_READ_TIMEOUT = 30


def _positive_int_env(name, default):
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        parsed = int(raw)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def get_max_artifact_bytes():
    return _positive_int_env("FEEDBACK_MAX_ARTIFACT_BYTES", DEFAULT_MAX_ARTIFACT_BYTES)


def get_max_preview_bytes():
    return _positive_int_env("FEEDBACK_MAX_PREVIEW_BYTES", DEFAULT_MAX_PREVIEW_BYTES)


def _validate_public_http_url(url):
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Only http/https URLs are allowed.")

    hostname = parsed.hostname
    if not hostname:
        raise ValueError("URL is missing a hostname.")

    try:
        ip = ipaddress.ip_address(hostname)
        resolved_ips = [ip]
    except ValueError:
        try:
            infos = socket.getaddrinfo(hostname, parsed.port or 443, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise ValueError(f"Could not resolve host: {hostname}") from exc

        resolved_ips = []
        for info in infos:
            candidate = info[4][0]
            try:
                resolved_ips.append(ipaddress.ip_address(candidate))
            except ValueError:
                continue

    if not resolved_ips:
        raise ValueError(f"Could not resolve any IP address for host: {hostname}")

    for ip in resolved_ips:
        if any(
            [
                ip.is_private,
                ip.is_loopback,
                ip.is_link_local,
                ip.is_multicast,
                ip.is_unspecified,
                ip.is_reserved,
            ]
        ):
            raise ValueError(f"Blocked non-public host: {hostname}")


def fetch_remote_bytes(url, max_bytes):
    _validate_public_http_url(url)

    response = requests.get(
        url,
        stream=True,
        timeout=(DEFAULT_CONNECT_TIMEOUT, DEFAULT_READ_TIMEOUT),
    )
    response.raise_for_status()

    declared_size = response.headers.get("Content-Length")
    if declared_size:
        try:
            if int(declared_size) > max_bytes:
                raise ValueError(f"Remote file too large: {declared_size} bytes exceeds limit {max_bytes}")
        except ValueError as exc:
            if "exceeds limit" in str(exc):
                response.close()
                raise

    chunks = []
    total = 0
    try:
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            total += len(chunk)
            if total > max_bytes:
                raise ValueError(f"Remote file exceeds limit of {max_bytes} bytes")
            chunks.append(chunk)
    finally:
        response.close()

    return b"".join(chunks)


def fetch_remote_text(url, max_bytes):
    raw = fetch_remote_bytes(url, max_bytes=max_bytes)
    return raw.decode("utf-8", errors="ignore")


def download_remote_file(url, destination_path):
    payload = fetch_remote_bytes(url, max_bytes=get_max_artifact_bytes())
    with open(destination_path, "wb") as handle:
        handle.write(payload)
