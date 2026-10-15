"""CLI: python -m tools.cross_network_acceptance {run,role,build-sidecar}."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from tools.cross_network_acceptance.run import git_sha, run_case
from tools.cross_network_acceptance.roles import run_role
from tools.cross_network_acceptance.sidecar import SidecarError, build_sidecar, product_sha

REPO_ROOT = Path(__file__).resolve().parents[2]


def _maybe_sudo() -> None:
    if os.geteuid() == 0:
        return
    os.execvp(
        "sudo",
        [
            "sudo",
            "-n",
            "-E",
            sys.executable,
            "-m",
            "tools.cross_network_acceptance",
            *sys.argv[1:],
        ],
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Same-LAN and two-NAT Host/Join acceptance harness."
    )
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="Build topology and drive host/join.")
    run.add_argument("--case", choices=("same-lan", "cross-network"), required=True)
    run.add_argument(
        "--profiles",
        default="art,music",
        help="Comma-separated profiles (art, music).",
    )
    run.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / "out" / "cross-network-acceptance",
    )
    run.add_argument("--sha", default="")
    run.add_argument("--netem", action="store_true", default=None)
    run.add_argument("--no-netem", action="store_true")
    run.add_argument("--keep-workdir", action="store_true")
    run.add_argument(
        "--nat",
        choices=("masquerade", "cone", "symmetric"),
        default="masquerade",
        help="Cross-network NAT behaviour (cone/full vs symmetric).",
    )
    run.add_argument(
        "--product-root",
        type=Path,
        default=None,
        help="Product checkout to import (PR #372 worktree). Defaults to this repo.",
    )
    run.add_argument(
        "--transport-binary",
        default="",
        help="Path to webjam-fabric. Sets WEBJAM_TRANSPORT_BINARY for source checkouts.",
    )
    run.add_argument(
        "--build-id",
        default="",
        help="Sidecar build id (WEBJAM_BUILD_ID). Defaults to --sha.",
    )
    run.add_argument(
        "--expected-status",
        choices=("PASS", "FAIL"),
        default="PASS",
        help=(
            "CI exit-code target. FAIL still writes a FAIL receipt; the job "
            "is green only when that FAIL is honest (harness_ok)."
        ),
    )

    role = sub.add_parser("role", help="Internal host/guest worker.")
    role.add_argument("--role", choices=("host", "guest"), required=True)
    role.add_argument("--profile", choices=("art", "music"), required=True)
    role.add_argument("--work-dir", type=Path, required=True)

    build = sub.add_parser("build-sidecar", help="Compile webjam-fabric from a product tree.")
    build.add_argument("--product-root", type=Path, required=True)
    build.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / "out" / "cross-network-acceptance" / "webjam-fabric",
    )

    args = parser.parse_args(argv)
    if args.command == "role":
        return run_role(args.role, args.profile, args.work_dir)
    if args.command == "build-sidecar":
        try:
            path = build_sidecar(args.product_root.resolve(), args.output)
        except SidecarError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(path)
        return 0
    if args.command != "run":
        parser.print_help()
        return 2
    _maybe_sudo()
    profiles = tuple(
        item.strip() for item in str(args.profiles).split(",") if item.strip()
    )
    if not profiles or any(item not in {"art", "music"} for item in profiles):
        print("Profiles must be art and/or music.", file=sys.stderr)
        return 2
    netem = True
    if args.no_netem:
        netem = False
    elif args.netem is True:
        netem = True
    elif args.case == "same-lan":
        netem = False
    product = args.product_root.resolve() if args.product_root else REPO_ROOT
    sha = str(args.sha or git_sha(product if args.product_root else REPO_ROOT))
    build_id = str(args.build_id or (product_sha(product) if args.transport_binary else sha))
    return run_case(
        case=args.case,
        profiles=profiles,
        output_dir=args.output_dir,
        netem=netem,
        sha=sha,
        keep_workdir=bool(args.keep_workdir),
        nat_mode=str(args.nat),
        product_root=product,
        transport_binary=str(args.transport_binary or ""),
        build_id=build_id,
        expected_status=str(args.expected_status),
    )


if __name__ == "__main__":
    raise SystemExit(main())
