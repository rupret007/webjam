"""CLI."""

import argparse
import os
import sys
from pathlib import Path

from tools.cross_network_acceptance.proof import run_peer
from tools.cross_network_acceptance.run import run_case
from tools.cross_network_acceptance.sidecar import SidecarError, build_sidecar, product_sha

REPO_ROOT = Path(__file__).resolve().parents[2]


def _maybe_sudo() -> None:
    if os.geteuid() != 0:
        os.execvp("sudo", ["sudo", "-n", "-E", sys.executable,
                           "-m", "tools.cross_network_acceptance", *sys.argv[1:]])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    run = sub.add_parser("run")
    run.add_argument("--case", choices=("same-lan", "cone", "relay"), required=True)
    run.add_argument("--output-dir", type=Path, default=REPO_ROOT / "out" / "cross-network-acceptance")
    run.add_argument("--sha", default="")
    run.add_argument("--netem", action="store_true")
    run.add_argument("--no-netem", action="store_true")
    run.add_argument("--keep-workdir", action="store_true")
    run.add_argument("--product-root", type=Path, default=None)
    run.add_argument("--transport-binary", default="")
    run.add_argument("--build-id", default="")
    run.add_argument("--job-id", default="")
    peer = sub.add_parser("peer")
    peer.add_argument("--role", choices=("host", "guest"), required=True)
    peer.add_argument("--work-dir", type=Path, required=True)
    build = sub.add_parser("build-sidecar")
    build.add_argument("--product-root", type=Path, required=True)
    build.add_argument("--output", type=Path, default=REPO_ROOT / "out" / "cross-network-acceptance" / "webjam-fabric")
    probe = sub.add_parser("probe-nat")
    probe.add_argument("--nat", action="append", dest="nats", choices=("cone", "symmetric"))

    args = parser.parse_args(argv)
    if args.command == "peer":
        return run_peer(args.work_dir, args.role)
    if args.command == "probe-nat":
        from tools.cross_network_acceptance.nat_probe import run_probes

        _maybe_sudo()
        modes = tuple(args.nats) if args.nats else ("cone", "symmetric")
        return run_probes(modes)
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
    netem = False if args.no_netem or args.case == "same-lan" else bool(args.netem)
    product = args.product_root.resolve() if args.product_root else REPO_ROOT
    sha = str(args.sha or product_sha(product if args.product_root else REPO_ROOT))
    build_id = str(args.build_id or (product_sha(product) if args.transport_binary else sha))
    return run_case(
        case=args.case, output_dir=args.output_dir, netem=netem, sha=sha,
        keep_workdir=bool(args.keep_workdir), product_root=product,
        transport_binary=str(args.transport_binary or ""),
        build_id=build_id, job_id=str(args.job_id or ""),
    )


if __name__ == "__main__":
    raise SystemExit(main())
