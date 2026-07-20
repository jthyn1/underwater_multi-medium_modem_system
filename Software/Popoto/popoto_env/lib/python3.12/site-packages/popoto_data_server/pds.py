#!/usr/bin/env python3
"""Lightweight data server for Popoto modems that reports component versions.

This server runs on the modem and responds with the versions of key
Popoto applications when a client connects to the configured port.
"""

from __future__ import annotations

import argparse
import json
import logging
import socket
import socketserver
import subprocess
import sys
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List

DEFAULT_PORT = 39484
DEFAULT_POPOTO_APP_HOST = "127.0.0.1"
DEFAULT_POPOTO_APP_PORT = 17000
POPOTO_APP_TIMEOUT_SECONDS = 3

def _get_version(package: str) -> str:
    """Return the installed version of the given package or 'unknown'."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "show", package],
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        logging.debug("Package %s not found via pip.", package)
        return "unknown"

    for line in result.stdout.splitlines():
        if line.lower().startswith("version:"):
            version = line.split(":", 1)[1].strip() or "unknown"
            logging.debug("Fetched package version: %s -> %s", package, version)
            return version

    return "unknown"


def _run_simple_command(cmd: List[str]) -> str:
    """Run a command, returning stdout or '' on failure."""
    try:
        result = subprocess.run(
            cmd, check=True, capture_output=True, text=True, timeout=2
        )
        stdout = result.stdout.strip()
        logging.debug("Command succeeded: %s -> %s", " ".join(cmd), stdout)
        return stdout
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logging.debug("Command failed: %s (%s)", " ".join(cmd), exc)
        return ""


def _get_os_version() -> str:
    """Return OS version from uname -a."""
    logging.debug("Fetching OS version via uname -a.")
    version = _run_simple_command(["uname", "-a"]) or "unknown"
    logging.debug("OS version: %s", version)
    return version


def _get_maria_version() -> str:
    """Return the MARIA version string if available."""
    logging.debug("Fetching MARIA version via /opt/maria/bin/Maria_app -v.")
    version = _run_simple_command(["/opt/maria/bin/Maria_app", "-v"])
    if version:
        maria_ver = version.splitlines()[0].strip()
        logging.debug("MARIA version: %s", maria_ver)
        return maria_ver
    logging.debug("MARIA version unavailable.")
    return "unknown"


def _get_popoto_app_version(host: str, port: int) -> object:
    """Query popoto_app over its command socket for version information."""
    request = '{ "Command": "getVersion", "Arguments": "Unused Arguments"}\n'
    logging.debug("Querying popoto_app at %s:%s", host, port)
    target_field = "Info"
    try:
        with socket.create_connection(
            (host, port), timeout=POPOTO_APP_TIMEOUT_SECONDS
        ) as sock:
            sock.settimeout(POPOTO_APP_TIMEOUT_SECONDS)
            sock.sendall(request.encode("utf-8"))
            chunks: List[bytes] = []
            while True:
                try:
                    data = sock.recv(4096)
                except socket.timeout:
                    break
                if not data:
                    break
                chunks.append(data)
    except OSError as exc:
        logging.warning(
            "Unable to query popoto_app at %s:%s: %s", host, port, exc
        )
        return "unknown"

    raw_reply = b"".join(chunks).decode("utf-8", errors="ignore")
    if not raw_reply.strip():
        logging.debug("popoto_app version response was empty.")
        return "unknown"

    logging.debug("Raw popoto_app reply: %s", raw_reply.strip().replace("\n", "\\n"))

    # Extract the JSON object containing "Popoto Modem Version".
    match = re.search(r'\{[^{}]*"Popoto Modem Version[^{}]*\}', raw_reply)
    if match:
        candidate = match.group(0)
        try:
            parsed = json.loads(candidate)
            logging.debug("Parsed popoto_app version JSON: %s", parsed)
            value = parsed.get(target_field)
            if isinstance(value, str):
                return value
            return parsed
        except json.JSONDecodeError:
            logging.warning(
                "Failed to parse popoto_app version JSON; returning raw candidate: %s",
                candidate,
            )
            return candidate

    logging.debug("No popoto_app version JSON found; returning full payload.")
    return raw_reply.strip()


def collect_versions(popoto_host: str, popoto_port: int) -> List[Dict[str, object]]:
    """Collect versions for Popoto components."""
    logging.debug("Starting version collection.")

    tasks = {
        "popoto-api": lambda: {"popoto-api": _get_version("popoto-api")},
        "popoto-shell": lambda: {"popoto-shell": _get_version("popoto-shell")},
        "popoto-web-ui": lambda: {"popoto-web-ui": _get_version("popoto-web-ui")},
        "popoto_app": lambda: {"popoto_app": _get_popoto_app_version(popoto_host, popoto_port)},
        "maria": lambda: {"maria": _get_maria_version()},
        "os": lambda: {"os": _get_os_version()},
    }

    results: List[Dict[str, object]] = []
    with ThreadPoolExecutor(max_workers=len(tasks)) as executor:
        future_map = {executor.submit(func): key for key, func in tasks.items()}
        for future in as_completed(future_map):
            key = future_map[future]
            try:
                result = future.result()
                logging.debug("Collected %s version info: %s", key, result)
                if isinstance(result, list):
                    results.extend(result)
                elif isinstance(result, dict):
                    results.append(result)
            except Exception as exc:
                logging.warning("Failed to collect version info for %s: %s", key, exc)
    logging.debug("Completed version collection.")
    return results


class VersionRequestHandler(socketserver.BaseRequestHandler):
    """Respond to any connection with the current version list."""

    def handle(self) -> None:
        try:
            logging.debug("Version request from %s", self.client_address)
            payload = json.dumps(
                collect_versions(
                    popoto_host=self.server.popoto_host,  # type: ignore[attr-defined]
                    popoto_port=self.server.popoto_port,  # type: ignore[attr-defined]
                )
            ) + "\n"
            logging.debug("Version payload: %s", payload.strip())
            self.request.sendall(payload.encode("utf-8"))
        except Exception as exc:  # pragma: no cover - defensive
            logging.exception("Failed to handle version request: %s", exc)


class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True


def run_server(
    port: int = DEFAULT_PORT,
    popoto_host: str = DEFAULT_POPOTO_APP_HOST,
    popoto_port: int = DEFAULT_POPOTO_APP_PORT,
) -> None:
    """Start the data server."""
    with ThreadedTCPServer(("0.0.0.0", port), VersionRequestHandler) as server:
        server.popoto_host = popoto_host  # type: ignore[attr-defined]
        server.popoto_port = popoto_port  # type: ignore[attr-defined]
        logging.info(
            "Popoto data server listening on port %d (popoto_app %s:%d)",
            port,
            popoto_host,
            popoto_port,
        )
        server.serve_forever()


def parse_args():
    parser = argparse.ArgumentParser(
        description="Popoto data server (collects versions from modem services)."
    )
    parser.add_argument(
        "--listen-port",
        type=int,
        default=DEFAULT_PORT,
        help=f"Port for the data server (default {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--popoto-host",
        default=DEFAULT_POPOTO_APP_HOST,
        help=f"Host/IP for the popoto_app command socket (default {DEFAULT_POPOTO_APP_HOST})",
    )
    parser.add_argument(
        "--popoto-port",
        type=int,
        default=DEFAULT_POPOTO_APP_PORT,
        help=f"Port for the popoto_app command socket (default {DEFAULT_POPOTO_APP_PORT})",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose logging (DEBUG level).",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stdout,
    )

    run_server(
        port=args.listen_port, popoto_host=args.popoto_host, popoto_port=args.popoto_port
    )


if __name__ == "__main__":
    main()
