"""Command-line interface for Service Dependency Mapper."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from service_dependency_mapper import __version__
from service_dependency_mapper.analyzer import analyze
from service_dependency_mapper.config import ConfigError, load_config, topological_order
from service_dependency_mapper.discovery import (
    DiscoveryError,
    DiscoverySettings,
    discover_infrastructure,
    network_targets_from_cidrs,
    write_discovery_map,
)
from service_dependency_mapper.graph import render_dot, render_mermaid
from service_dependency_mapper.models import OverallStatus
from service_dependency_mapper.performance import resolve_worker_count
from service_dependency_mapper.reporting import render_json, render_table


def _workers_argument(value: str) -> int | str:
    if value.strip().lower() == "auto":
        return "auto"
    try:
        return resolve_worker_count(
            value,
            workload="analysis",
            location="--workers",
        )
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sdmap",
        description=(
            "Discover infrastructure, map service dependencies, run active checks, "
            "and identify root-cause candidates."
        ),
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    check = commands.add_parser("check", help="Run checks and analyze dependencies.")
    check.add_argument("config", type=Path, help="Path to a YAML service map.")
    check.add_argument(
        "--format",
        choices=("table", "json"),
        default="table",
        help="Report format (default: table).",
    )
    check.add_argument("-o", "--output", type=Path, help="Write the report to a file.")
    check.add_argument(
        "--timeout", type=float, help="Override the default timeout for every check."
    )
    check.add_argument(
        "--workers",
        type=_workers_argument,
        help="Override concurrent workers: auto or 1-256.",
    )
    check.add_argument(
        "--no-color", action="store_true", help="Disable ANSI colors in table output."
    )

    validate = commands.add_parser(
        "validate", help="Validate YAML and dependency graph without network checks."
    )
    validate.add_argument("config", type=Path, help="Path to a YAML service map.")

    graph = commands.add_parser("graph", help="Export the dependency graph.")
    graph.add_argument("config", type=Path, help="Path to a YAML service map.")
    graph.add_argument(
        "--format",
        choices=("mermaid", "dot"),
        default="mermaid",
        help="Graph format (default: mermaid).",
    )
    graph.add_argument("-o", "--output", type=Path, help="Write graph to a file.")

    discover = commands.add_parser(
        "discover",
        help="Discover private IPv4 networks, hosts, and generic TCP services.",
    )
    discover.add_argument(
        "-n",
        "--network",
        action="append",
        default=[],
        metavar="CIDR",
        help=(
            "Private IPv4 network to scan; repeat for multiple networks. "
            "Connected networks are detected automatically when omitted."
        ),
    )
    discover.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("discovered-infrastructure.yaml"),
        help="Generated YAML map (default: discovered-infrastructure.yaml).",
    )
    discover.add_argument(
        "--timeout",
        type=float,
        default=0.3,
        help="Per-probe timeout in seconds (default: 0.3).",
    )
    discover.add_argument(
        "--workers",
        type=_workers_argument,
        default="auto",
        help="Concurrent discovery workers: auto or 1-256 (default: auto).",
    )
    discover.add_argument(
        "--max-hosts",
        type=int,
        default=1022,
        help="Maximum addresses per network from 1 to 4094 (default: 1022).",
    )

    gui = commands.add_parser("gui", help="Launch the desktop interface.")
    gui.add_argument(
        "config",
        nargs="?",
        type=Path,
        help="Optional YAML service map to preselect.",
    )
    return parser


def _write_or_print(content: str, output: Path | None) -> None:
    if output is None:
        print(content, end="")
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content, encoding="utf-8")
    print(f"Saved: {output}")


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return a process exit code."""

    args = _parser().parse_args(argv)
    try:
        if args.command == "gui":
            try:
                from service_dependency_mapper.gui import (
                    GuiUnavailableError,
                    launch_gui,
                )
            except ImportError:
                print(
                    "GUI error: Tkinter is not available in this Python installation.",
                    file=sys.stderr,
                )
                return 2
            try:
                return launch_gui(args.config)
            except GuiUnavailableError as exc:
                print(f"GUI error: {exc}", file=sys.stderr)
                return 2

        if args.command == "validate":
            dependency_map = load_config(args.config)
            order = " -> ".join(topological_order(dependency_map))
            print(
                f"Configuration valid: {dependency_map.service_name} "
                f"({len(dependency_map.components)} components)"
            )
            print(f"Dependency-first order: {order}")
            return 0

        if args.command == "graph":
            dependency_map = load_config(args.config)
            content = (
                render_mermaid(dependency_map)
                if args.format == "mermaid"
                else render_dot(dependency_map)
            )
            _write_or_print(content, args.output)
            return 0

        if args.command == "discover":
            discovery_workers = resolve_worker_count(
                args.workers,
                workload="discovery",
                location="--workers",
            )
            settings = DiscoverySettings(
                timeout=args.timeout,
                workers=discovery_workers,
                max_hosts_per_network=args.max_hosts,
            )
            networks = (
                network_targets_from_cidrs(
                    args.network,
                    max_hosts=args.max_hosts,
                )
                if args.network
                else None
            )
            result = discover_infrastructure(networks, settings=settings)
            output = write_discovery_map(result, args.output)
            print(
                f"Discovery complete: {len(result.hosts)} host(s), "
                f"{result.service_count} service(s)."
            )
            print(
                f"Performance: {result.worker_count} worker(s), "
                f"{result.logical_processors} logical processor(s)."
            )
            print(f"Saved: {output}")
            for warning in result.warnings:
                print(f"Safety note: {warning}", file=sys.stderr)
            return 0

        analysis_workers = (
            resolve_worker_count(
                args.workers,
                workload="analysis",
                location="--workers",
            )
            if args.workers is not None
            else None
        )
        dependency_map = load_config(
            args.config,
            timeout_override=args.timeout,
            workers_override=analysis_workers,
        )
        report = analyze(dependency_map)
        if args.format == "json":
            content = render_json(report)
        else:
            use_color = (
                not args.no_color and args.output is None and sys.stdout.isatty()
            )
            content = render_table(report, color=use_color)
        _write_or_print(content, args.output)
        return 0 if report.overall_status is OverallStatus.HEALTHY else 1

    except DiscoveryError as exc:
        print(f"Discovery error: {exc}", file=sys.stderr)
        return 2
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"Argument error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    except OSError as exc:
        print(f"I/O error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
