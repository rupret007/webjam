"""Command-line entry point for the WebJam reference service."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import signal
from collections.abc import Sequence
from pathlib import Path

from .config import ServiceConfig
from .server import ReferenceService


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw, 10)
    except ValueError as exc:
        raise SystemExit(f"{name} must be an integer") from exc


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    normalized = raw.casefold()
    if normalized in {"1", "true", "yes"}:
        return True
    if normalized in {"0", "false", "no"}:
        return False
    raise SystemExit(f"{name} must be true or false")


def build_parser() -> argparse.ArgumentParser:
    defaults = ServiceConfig()
    parser = argparse.ArgumentParser(
        prog="webjam-reference",
        description="WebJam v3 opaque rendezvous and exact-peer relay reference service",
    )
    parser.add_argument(
        "--control-bind", default=os.environ.get("WEBJAM_CONTROL_BIND", defaults.control_bind)
    )
    parser.add_argument(
        "--control-port",
        type=int,
        default=_env_int("WEBJAM_CONTROL_PORT", defaults.control_port),
    )
    parser.add_argument(
        "--relay-bind", default=os.environ.get("WEBJAM_RELAY_BIND", defaults.relay_bind)
    )
    parser.add_argument(
        "--relay-port",
        type=int,
        default=_env_int("WEBJAM_RELAY_PORT", defaults.relay_port),
    )
    parser.add_argument(
        "--http-bind", default=os.environ.get("WEBJAM_HTTP_BIND", defaults.http_bind)
    )
    parser.add_argument(
        "--http-port",
        type=int,
        default=_env_int("WEBJAM_HTTP_PORT", defaults.http_port),
    )
    parser.add_argument("--tls-cert", default=os.environ.get("WEBJAM_TLS_CERT"))
    parser.add_argument("--tls-key", default=os.environ.get("WEBJAM_TLS_KEY"))
    parser.add_argument(
        "--allow-insecure-public-control",
        action="store_true",
        default=_env_bool("WEBJAM_ALLOW_INSECURE_PUBLIC_CONTROL"),
        help="only for a trusted local TLS sidecar; never expose this mode directly",
    )
    parser.add_argument(
        "--max-sessions",
        type=int,
        default=_env_int("WEBJAM_MAX_SESSIONS", defaults.max_sessions),
    )
    parser.add_argument(
        "--max-bandwidth-bytes-per-second",
        type=int,
        default=_env_int(
            "WEBJAM_MAX_BANDWIDTH_BYTES_PER_SECOND",
            defaults.bandwidth_bytes_per_second,
        ),
    )
    parser.add_argument(
        "--max-session-ttl-seconds",
        type=int,
        default=_env_int(
            "WEBJAM_MAX_SESSION_TTL_SECONDS", defaults.max_session_ttl_seconds
        ),
    )
    parser.add_argument(
        "--idle-timeout-seconds",
        type=int,
        default=_env_int("WEBJAM_IDLE_TIMEOUT_SECONDS", defaults.idle_timeout_seconds),
    )
    return parser


def config_from_args(argv: Sequence[str] | None = None) -> ServiceConfig:
    args = build_parser().parse_args(argv)
    defaults = ServiceConfig()
    return ServiceConfig(
        control_bind=args.control_bind,
        control_port=args.control_port,
        relay_bind=args.relay_bind,
        relay_port=args.relay_port,
        http_bind=args.http_bind,
        http_port=args.http_port,
        tls_cert_path=Path(args.tls_cert) if args.tls_cert else None,
        tls_key_path=Path(args.tls_key) if args.tls_key else None,
        allow_insecure_public_control=args.allow_insecure_public_control,
        max_sessions=args.max_sessions,
        bandwidth_bytes_per_second=args.max_bandwidth_bytes_per_second,
        bandwidth_burst_bytes=max(
            args.max_bandwidth_bytes_per_second,
            args.max_bandwidth_bytes_per_second * 2,
        ),
        max_session_ttl_seconds=args.max_session_ttl_seconds,
        min_session_ttl_seconds=min(
            defaults.min_session_ttl_seconds, args.max_session_ttl_seconds
        ),
        idle_timeout_seconds=args.idle_timeout_seconds,
    )


async def run(config: ServiceConfig) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(signum, stop.set)
    service = ReferenceService(config)
    await service.start()
    try:
        await stop.wait()
    finally:
        await service.close()


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    config = config_from_args(argv)
    try:
        asyncio.run(run(config))
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
