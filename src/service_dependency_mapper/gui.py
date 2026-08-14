"""Tkinter desktop interface for Service Dependency Mapper."""

from __future__ import annotations

import argparse
import json
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
from collections.abc import Sequence
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from service_dependency_mapper import __version__
from service_dependency_mapper.analyzer import analyze
from service_dependency_mapper.config import ConfigError, load_config, topological_order
from service_dependency_mapper.discovery import (
    DiscoveryError,
    DiscoveryResult,
    DiscoverySettings,
    NetworkTarget,
    default_discovery_output,
    discover_infrastructure,
    host_component_id,
    network_targets_from_cidrs,
    service_component_id,
    write_discovery_map,
)
from service_dependency_mapper.graph import render_dot, render_mermaid
from service_dependency_mapper.models import (
    AnalysisReport,
    AnalyzedResult,
    Component,
    DependencyMap,
)
from service_dependency_mapper.performance import (
    Workload,
    automatic_worker_count,
    build_discovery_worker_plan,
    logical_processor_count,
    resolve_worker_count,
)
from service_dependency_mapper.reporting import render_json
from service_dependency_mapper.topology import build_topology_layout
from service_dependency_mapper.updater import (
    UpdateInfo,
    UpdateResult,
    fetch_update_info,
    install_update,
)

BACKGROUND = "#07111f"
PANEL = "#0d1d2b"
PANEL_ALT = "#102636"
BORDER = "#1e3a4a"
TEXT = "#e5f0f7"
MUTED = "#93a9b8"
CYAN = "#22d3ee"
GREEN = "#34d399"
YELLOW = "#fbbf24"
RED = "#fb7185"

DEFAULT_TEMPLATE = """version: 1

service:
  name: My service
  description: Dependency map created in the desktop interface.

defaults:
  timeout: 3
  workers: auto

components:
  - id: gateway_ping
    name: Internet gateway
    critical: false
    check:
      type: icmp
      target: 1.1.1.1
      count: 2

  - id: service_dns
    name: Service DNS
    check:
      type: dns
      target: example.com

  - id: service_tcp
    name: Service TCP endpoint
    depends_on: [service_dns]
    check:
      type: tcp
      host: example.com
      port: 443

  - id: service_tls
    name: Service certificate
    depends_on: [service_tcp]
    check:
      type: tls
      host: example.com
      port: 443
      min_days_remaining: 14

  - id: service_http
    name: Service website
    depends_on: [service_tls]
    check:
      type: http
      url: https://example.com/
      expected_status: [200]
"""


class GuiUnavailableError(RuntimeError):
    """Raised when the desktop environment cannot create a Tk window."""


def parse_timeout(value: str) -> float | None:
    """Parse an optional positive timeout override from a GUI entry."""

    value = value.strip()
    if not value:
        return None
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ValueError("Timeout must be a number greater than zero.") from exc
    if parsed <= 0:
        raise ValueError("Timeout must be a number greater than zero.")
    return parsed


def parse_workers(
    value: str,
    *,
    workload: Workload = "analysis",
) -> int | None:
    """Parse an optional worker count accepted by the configuration loader."""

    value = value.strip()
    if not value:
        return None
    return resolve_worker_count(
        value,
        workload=workload,
        location="Workers",
    )


def parse_discovery_targets(value: str) -> tuple[NetworkTarget, ...]:
    """Parse optional comma-, semicolon-, or whitespace-separated targets."""

    entries = tuple(item for item in re.split(r"[\s,;]+", value.strip()) if item)
    if not entries:
        return ()
    return network_targets_from_cidrs(entries)


def result_row(item: AnalyzedResult) -> tuple[str, str, str, str, str, str]:
    """Return stable Treeview values for one analyzed component."""

    latency = "—"
    if item.result.latency_ms is not None:
        latency = f"{item.result.latency_ms:.2f} ms"
    return (
        item.component.name,
        item.component.check_type.upper(),
        item.component.target,
        item.result.status.value.upper(),
        latency,
        item.diagnosis.value.replace("_", " ").upper(),
    )


def report_summary(report: AnalysisReport) -> dict[str, str]:
    """Return the values displayed in the four summary cards."""

    return {
        "overall": report.overall_status.value.upper(),
        "root_causes": ", ".join(report.root_causes) or "None",
        "components": str(len(report.results)),
        "duration": f"{report.duration_ms:.2f} ms",
    }


def discovery_detail_lines(
    discovery: DiscoveryResult | None,
    component_id: str,
) -> list[str]:
    """Return inventory details for a discovered host or service component."""

    if discovery is None:
        return []
    for host in discovery.hosts:
        if host_component_id(host.address) == component_id:
            return [
                (
                    f"Hostname: {host.hostname or 'unknown'}  |  "
                    f"MAC: {host.mac_address or 'unknown'}  |  "
                    f"ICMP: {'reply' if host.ping_responded else 'no reply'}"
                ),
                (
                    "Open TCP ports: "
                    + (
                        ", ".join(str(service.port) for service in host.services)
                        or "none detected"
                    )
                ),
            ]
        for service in host.services:
            if service_component_id(host.address, service.port) != component_id:
                continue
            lines = [
                (
                    f"Protocol: {service.protocol}  |  "
                    f"Secure: {'yes' if service.secure else 'no'}  |  "
                    f"HTTP status: {service.http_status or 'n/a'}"
                )
            ]
            if service.banner:
                lines.append(f"Banner: {service.banner}")
            return lines
    return []


def _shorten(value: str, length: int) -> str:
    if len(value) <= length:
        return value
    return f"{value[: length - 1]}…"


class TopologyWindow:
    """Interactive, dependency-aware network topology rendered with Tk Canvas."""

    def __init__(
        self,
        parent: tk.Tk,
        dependency_map: DependencyMap,
        report: AnalysisReport | None = None,
        discovery: DiscoveryResult | None = None,
    ) -> None:
        self.dependency_map = dependency_map
        self.report = report
        self.discovery = discovery
        self.results_by_id = (
            {item.component.component_id: item for item in report.results}
            if report
            else {}
        )
        self.layout = build_topology_layout(dependency_map)
        self.zoom_factor = 1.0

        self.window = tk.Toplevel(parent)
        self.window.title(f"Network topology — {dependency_map.service_name}")
        self.window.geometry("1320x820")
        self.window.minsize(860, 560)
        self.window.configure(background=BACKGROUND)
        self.window.rowconfigure(1, weight=1)
        self.window.columnconfigure(0, weight=1)

        self._build_header()
        self._build_canvas()
        self._build_details()
        self._draw()
        self.window.after(100, self.fit)

    def _build_header(self) -> None:
        header = tk.Frame(self.window, background=BACKGROUND, padx=18, pady=14)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)

        title = tk.Frame(header, background=BACKGROUND)
        title.grid(row=0, column=0, sticky="w")
        tk.Label(
            title,
            text=self.dependency_map.service_name,
            background=BACKGROUND,
            foreground=TEXT,
            font=("Segoe UI Semibold", 16),
        ).pack(anchor="w")
        tk.Label(
            title,
            text=(
                f"{len(self.dependency_map.components)} nodes • "
                "dependency → component • mouse wheel to zoom"
            ),
            background=BACKGROUND,
            foreground=MUTED,
            font=("Segoe UI", 9),
        ).pack(anchor="w", pady=(2, 0))

        legend = tk.Frame(header, background=BACKGROUND)
        legend.grid(row=0, column=1, padx=18)
        legend_items = (
            ("NETWORK", CYAN),
            ("HEALTHY", GREEN),
            ("IMPACTED", YELLOW),
            ("ROOT CAUSE", RED),
        )
        for text, color in legend_items:
            tk.Label(
                legend,
                text=f"● {text}",
                background=BACKGROUND,
                foreground=color,
                font=("Segoe UI Semibold", 8),
            ).pack(side="left", padx=5)

        controls = tk.Frame(header, background=BACKGROUND)
        controls.grid(row=0, column=2, sticky="e")
        ttk.Button(
            controls,
            text="−",
            style="Secondary.TButton",
            command=lambda: self.zoom(0.85),
            width=3,
        ).pack(side="left", padx=(0, 5))
        ttk.Button(
            controls,
            text="+",
            style="Secondary.TButton",
            command=lambda: self.zoom(1.18),
            width=3,
        ).pack(side="left", padx=(0, 5))
        ttk.Button(
            controls,
            text="Fit",
            style="Secondary.TButton",
            command=self.fit,
        ).pack(side="left")

    def _build_canvas(self) -> None:
        frame = tk.Frame(
            self.window,
            background=PANEL,
            highlightbackground=BORDER,
            highlightthickness=1,
        )
        frame.grid(row=1, column=0, sticky="nsew", padx=18)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        self.canvas = tk.Canvas(
            frame,
            background="#081522",
            highlightthickness=0,
            xscrollincrement=20,
            yscrollincrement=20,
        )
        horizontal = ttk.Scrollbar(
            frame, orient="horizontal", command=self.canvas.xview
        )
        vertical = ttk.Scrollbar(frame, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(
            xscrollcommand=horizontal.set,
            yscrollcommand=vertical.set,
        )
        self.canvas.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")

        self.canvas.bind("<MouseWheel>", self._mouse_wheel)
        self.canvas.bind("<Button-4>", lambda _event: self.zoom(1.1))
        self.canvas.bind("<Button-5>", lambda _event: self.zoom(0.9))
        self.canvas.bind("<ButtonPress-2>", self._pan_start)
        self.canvas.bind("<B2-Motion>", self._pan_move)

    def _build_details(self) -> None:
        self.details = tk.StringVar(
            value="Select a node to inspect its target, tags, dependencies, and state."
        )
        tk.Label(
            self.window,
            textvariable=self.details,
            background=BACKGROUND,
            foreground="#cbdce7",
            justify="left",
            anchor="w",
            padx=18,
            pady=12,
            wraplength=1220,
            font=("Consolas", 9),
        ).grid(row=2, column=0, sticky="ew")

    def _node_colors(self, component: Component) -> tuple[str, str]:
        result = self.results_by_id.get(component.component_id)
        if result:
            diagnosis = result.diagnosis.value
            if diagnosis == "root_cause":
                return "#351521", RED
            if diagnosis == "impacted":
                return "#352c12", YELLOW
            return "#102d29", GREEN
        if "network" in component.tags:
            return "#0a2a3a", CYAN
        if "host" in component.tags:
            return PANEL_ALT, "#67e8f9"
        return "#111d2a", "#6b8798"

    def _draw(self) -> None:
        self.canvas.delete("all")
        scale = self.zoom_factor

        for component in self.dependency_map.components:
            position = self.layout.nodes[component.component_id]
            target_x = position.center_x * scale
            target_y = position.top * scale
            for dependency_id in component.depends_on:
                dependency = self.layout.nodes[dependency_id]
                source_x = dependency.center_x * scale
                source_y = dependency.bottom * scale
                middle_y = source_y + ((target_y - source_y) / 2)
                self.canvas.create_line(
                    source_x,
                    source_y,
                    source_x,
                    middle_y,
                    target_x,
                    middle_y,
                    target_x,
                    target_y,
                    fill="#36556a",
                    width=max(1, round(2 * scale)),
                    arrow="last",
                    arrowshape=(
                        max(6, round(9 * scale)),
                        max(7, round(11 * scale)),
                        max(3, round(4 * scale)),
                    ),
                    tags=("edge",),
                )

        for component in self.dependency_map.components:
            position = self.layout.nodes[component.component_id]
            x1 = position.x * scale
            y1 = position.y * scale
            x2 = (position.x + position.width) * scale
            y2 = (position.y + position.height) * scale
            node_tag = f"node_{component.component_id}"
            fill, outline = self._node_colors(component)
            self.canvas.create_rectangle(
                x1,
                y1,
                x2,
                y2,
                fill=fill,
                outline=outline,
                width=max(1, round(2 * scale)),
                tags=("node", node_tag),
            )
            self.canvas.create_oval(
                x1 + (10 * scale),
                y1 + (12 * scale),
                x1 + (18 * scale),
                y1 + (20 * scale),
                fill=outline,
                outline="",
                tags=("node", node_tag),
            )
            self.canvas.create_text(
                x1 + (26 * scale),
                y1 + (11 * scale),
                text=_shorten(component.name, 34),
                anchor="nw",
                fill=TEXT,
                width=max(80, (position.width - 35) * scale),
                font=("Segoe UI Semibold", max(7, round(10 * scale))),
                tags=("node", node_tag),
            )
            subtitle = (
                f"{component.check_type.upper()} • {_shorten(component.target, 31)}"
            )
            self.canvas.create_text(
                x1 + (12 * scale),
                y1 + (48 * scale),
                text=subtitle,
                anchor="nw",
                fill=MUTED,
                width=max(80, (position.width - 24) * scale),
                font=("Consolas", max(6, round(8 * scale))),
                tags=("node", node_tag),
            )
            self.canvas.tag_bind(
                node_tag,
                "<Button-1>",
                lambda _event, component_id=component.component_id: self.select_node(
                    component_id
                ),
            )

        width = self.layout.width * scale
        height = self.layout.height * scale
        self.canvas.configure(scrollregion=(0, 0, width, height))

    def select_node(self, component_id: str) -> None:
        component = self.dependency_map.components_by_id[component_id]
        result = self.results_by_id.get(component_id)
        status = "NOT ANALYZED"
        if result:
            status = (
                f"{result.result.status.value.upper()} / "
                f"{result.diagnosis.value.replace('_', ' ').upper()}"
            )
        lines = [
            f"{component.name} ({component.component_id})  |  "
            f"Target: {component.target}  |  Status: {status}",
            f"Depends on: {', '.join(component.depends_on) or 'none'}  |  "
            f"Tags: {', '.join(component.tags) or 'none'}",
        ]
        lines.extend(discovery_detail_lines(self.discovery, component_id))
        self.details.set("\n".join(lines))

    def zoom(self, multiplier: float) -> None:
        self.zoom_factor = min(2.2, max(0.3, self.zoom_factor * multiplier))
        self._draw()

    def fit(self) -> None:
        self.window.update_idletasks()
        available_width = max(300, self.canvas.winfo_width() - 30)
        available_height = max(250, self.canvas.winfo_height() - 30)
        self.zoom_factor = min(
            1.25,
            max(
                0.3,
                min(
                    available_width / self.layout.width,
                    available_height / self.layout.height,
                ),
            ),
        )
        self._draw()
        self.canvas.xview_moveto(0)
        self.canvas.yview_moveto(0)

    def _mouse_wheel(self, event: tk.Event) -> None:
        self.zoom(1.1 if event.delta > 0 else 0.9)

    def _pan_start(self, event: tk.Event) -> None:
        self.canvas.scan_mark(event.x, event.y)

    def _pan_move(self, event: tk.Event) -> None:
        self.canvas.scan_dragto(event.x, event.y, gain=1)


class ServiceDependencyMapperGui:
    """Responsive desktop UI backed by the existing analysis engine."""

    def __init__(self, root: tk.Tk, config_path: str | Path | None = None) -> None:
        self.root = root
        self.root.title(f"Service Dependency Mapper {__version__}")
        self.root.geometry("1240x850")
        self.root.minsize(960, 680)
        self.root.configure(background=BACKGROUND)

        self.current_map: DependencyMap | None = None
        self.current_report: AnalysisReport | None = None
        self.current_discovery: DiscoveryResult | None = None
        self.discovery_path: Path | None = None
        self.results_by_id: dict[str, AnalyzedResult] = {}
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.discovery_cancel = threading.Event()
        self.closed = False
        self.busy = False
        self.latest_update: UpdateInfo | None = None
        self.update_check_running = False
        self.update_install_running = False
        self.update_installed = False

        self.config_path = tk.StringVar(
            value=str(Path(config_path)) if config_path else ""
        )
        self.timeout = tk.StringVar()
        self.workers = tk.StringVar(value="Auto")
        self.discovery_targets = tk.StringVar()
        self.status = tk.StringVar(
            value=(
                "Select a YAML map or discover the connected infrastructure "
                "automatically."
            )
        )
        self.overall = tk.StringVar(value="NOT RUN")
        self.root_causes = tk.StringVar(value="—")
        self.component_count = tk.StringVar(value="0")
        self.duration = tk.StringVar(value="—")

        self._configure_styles()
        self._build_layout()
        self._bind_shortcuts()
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.root.after(100, self._poll_events)
        self.root.after(700, lambda: self.check_updates(silent=True))

    def _configure_styles(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(
            ".",
            background=BACKGROUND,
            foreground=TEXT,
            fieldbackground=PANEL_ALT,
            bordercolor=BORDER,
            lightcolor=BORDER,
            darkcolor=BORDER,
            font=("Segoe UI", 10),
        )
        style.configure("TFrame", background=BACKGROUND)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("TLabel", background=BACKGROUND, foreground=TEXT)
        style.configure(
            "Muted.TLabel",
            background=BACKGROUND,
            foreground=MUTED,
            font=("Segoe UI", 9),
        )
        style.configure(
            "Section.TLabel",
            background=PANEL,
            foreground=TEXT,
            font=("Segoe UI Semibold", 11),
        )
        style.configure(
            "Accent.TButton",
            background=CYAN,
            foreground=BACKGROUND,
            borderwidth=0,
            padding=(16, 9),
            font=("Segoe UI Semibold", 10),
        )
        style.map(
            "Accent.TButton",
            background=[("active", "#67e8f9"), ("disabled", "#365563")],
            foreground=[("disabled", "#8798a2")],
        )
        style.configure(
            "Secondary.TButton",
            background=PANEL_ALT,
            foreground=TEXT,
            borderwidth=1,
            padding=(13, 8),
        )
        style.map(
            "Secondary.TButton",
            background=[("active", "#18384c"), ("disabled", "#152632")],
            foreground=[("disabled", "#607482")],
        )
        style.configure(
            "TEntry",
            fieldbackground=PANEL_ALT,
            foreground=TEXT,
            insertcolor=TEXT,
            padding=8,
        )
        style.configure(
            "Treeview",
            background=PANEL,
            fieldbackground=PANEL,
            foreground=TEXT,
            rowheight=34,
            borderwidth=0,
        )
        style.configure(
            "Treeview.Heading",
            background=PANEL_ALT,
            foreground="#c9dce8",
            padding=(8, 10),
            relief="flat",
            font=("Segoe UI Semibold", 9),
        )
        style.map(
            "Treeview",
            background=[("selected", "#164e63")],
            foreground=[("selected", "#ecfeff")],
        )
        style.configure(
            "Horizontal.TProgressbar",
            troughcolor=PANEL_ALT,
            background=CYAN,
            bordercolor=PANEL_ALT,
            lightcolor=CYAN,
            darkcolor=CYAN,
        )

    def _build_layout(self) -> None:
        container = ttk.Frame(self.root, padding=(24, 20))
        container.grid(row=0, column=0, sticky="nsew")
        self.root.rowconfigure(0, weight=1)
        self.root.columnconfigure(0, weight=1)
        container.columnconfigure(0, weight=1)
        container.rowconfigure(4, weight=1)

        self._build_header(container)
        self._build_controls(container)
        self._build_summary(container)
        self._build_results(container)
        self._build_footer(container)

    def _build_header(self, parent: ttk.Frame) -> None:
        header = tk.Frame(parent, background=BACKGROUND)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 16))
        header.columnconfigure(1, weight=1)

        mark = tk.Canvas(
            header,
            width=52,
            height=52,
            background=BACKGROUND,
            highlightthickness=0,
        )
        mark.grid(row=0, column=0, rowspan=2, padx=(0, 14))
        mark.create_rectangle(5, 5, 47, 47, outline=CYAN, width=2)
        mark.create_line(15, 18, 26, 18, 26, 34, 38, 34, fill=GREEN, width=3)
        mark.create_oval(11, 14, 19, 22, fill=CYAN, outline="")
        mark.create_oval(22, 30, 30, 38, fill=GREEN, outline="")
        mark.create_oval(34, 30, 42, 38, fill=GREEN, outline="")

        tk.Label(
            header,
            text="SERVICE DEPENDENCY MAPPER",
            background=BACKGROUND,
            foreground=TEXT,
            font=("Segoe UI Semibold", 20),
        ).grid(row=0, column=1, sticky="sw")
        tk.Label(
            header,
            text="Dependency-aware service diagnostics for NOC workflows",
            background=BACKGROUND,
            foreground=MUTED,
            font=("Segoe UI", 10),
        ).grid(row=1, column=1, sticky="nw")
        self.version_badge = tk.Label(
            header,
            text=f"v{__version__}",
            background=PANEL_ALT,
            foreground=CYAN,
            padx=10,
            pady=5,
            font=("Consolas", 9, "bold"),
        )
        self.version_badge.grid(row=0, column=2, sticky="e")
        self.update_button = ttk.Button(
            header,
            text="Check for updates",
            style="Secondary.TButton",
            command=self.update_or_check,
        )
        self.update_button.grid(row=1, column=2, sticky="e", pady=(4, 0))

    def _build_controls(self, parent: ttk.Frame) -> None:
        panel = ttk.Frame(parent, style="Panel.TFrame", padding=16)
        panel.grid(row=1, column=0, sticky="ew", pady=(0, 14))
        panel.columnconfigure(0, weight=1)

        ttk.Label(panel, text="SERVICE MAP", style="Section.TLabel").grid(
            row=0, column=0, columnspan=6, sticky="w", pady=(0, 9)
        )
        self.path_entry = ttk.Entry(panel, textvariable=self.config_path)
        self.path_entry.grid(row=1, column=0, sticky="ew", padx=(0, 8))

        self.browse_button = ttk.Button(
            panel,
            text="Browse…",
            style="Secondary.TButton",
            command=self.browse,
        )
        self.browse_button.grid(row=1, column=1, padx=(0, 8))
        self.template_button = ttk.Button(
            panel,
            text="New template",
            style="Secondary.TButton",
            command=self.create_template,
        )
        self.template_button.grid(row=1, column=2, padx=(0, 8))
        self.discover_button = ttk.Button(
            panel,
            text="Discover infrastructure",
            style="Secondary.TButton",
            command=self.discover,
        )
        self.discover_button.grid(row=1, column=3, padx=(0, 8))
        self.validate_button = ttk.Button(
            panel,
            text="Validate",
            style="Secondary.TButton",
            command=self.validate,
        )
        self.validate_button.grid(row=1, column=4, padx=(0, 8))
        self.run_button = ttk.Button(
            panel,
            text="Run analysis",
            style="Accent.TButton",
            command=self.run,
        )
        self.run_button.grid(row=1, column=5)

        options = tk.Frame(panel, background=PANEL)
        options.grid(row=2, column=0, columnspan=6, sticky="ew", pady=(13, 0))
        tk.Label(
            options,
            text="Optional overrides",
            background=PANEL,
            foreground=MUTED,
            font=("Segoe UI", 9),
        ).pack(side="left", padx=(0, 12))
        tk.Label(
            options,
            text="Timeout:",
            background=PANEL,
            foreground=MUTED,
            font=("Segoe UI", 9),
        ).pack(side="left")
        ttk.Entry(options, textvariable=self.timeout, width=8).pack(
            side="left", padx=(6, 14)
        )
        tk.Label(
            options,
            text="Parallelism:",
            background=PANEL,
            foreground=MUTED,
            font=("Segoe UI", 9),
        ).pack(side="left")
        ttk.Entry(options, textvariable=self.workers, width=8).pack(
            side="left", padx=(6, 0)
        )
        tk.Label(
            options,
            text=(
                f"Auto: {automatic_worker_count('discovery')} scan workers / "
                f"{logical_processor_count()} logical CPUs"
            ),
            background=PANEL,
            foreground=MUTED,
            font=("Segoe UI", 9),
        ).pack(side="left", padx=(12, 0))

        targets = tk.Frame(panel, background=PANEL)
        targets.grid(row=3, column=0, columnspan=6, sticky="ew", pady=(10, 0))
        targets.columnconfigure(1, weight=1)
        tk.Label(
            targets,
            text="Extra server IPs/CIDRs:",
            background=PANEL,
            foreground=MUTED,
            font=("Segoe UI", 9),
        ).grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.target_entry = ttk.Entry(
            targets,
            textvariable=self.discovery_targets,
        )
        self.target_entry.grid(row=0, column=1, sticky="ew")
        tk.Label(
            targets,
            text="Exact IP = all 65,535 TCP ports; separate targets with commas",
            background=PANEL,
            foreground=MUTED,
            font=("Segoe UI", 9),
        ).grid(row=0, column=2, sticky="e", padx=(10, 0))

    def _build_summary(self, parent: ttk.Frame) -> None:
        summary = tk.Frame(parent, background=BACKGROUND)
        summary.grid(row=2, column=0, sticky="ew", pady=(0, 14))
        for column in range(4):
            summary.columnconfigure(column, weight=1, uniform="summary")

        cards = (
            ("OVERALL", self.overall, CYAN),
            ("ROOT CAUSE", self.root_causes, RED),
            ("COMPONENTS", self.component_count, GREEN),
            ("DURATION", self.duration, YELLOW),
        )
        for column, (title, variable, color) in enumerate(cards):
            card = tk.Frame(
                summary,
                background=PANEL,
                highlightbackground=BORDER,
                highlightthickness=1,
                padx=15,
                pady=12,
            )
            card.grid(
                row=0,
                column=column,
                sticky="ew",
                padx=(0, 8) if column < 3 else 0,
            )
            tk.Label(
                card,
                text=title,
                background=PANEL,
                foreground=MUTED,
                font=("Segoe UI Semibold", 8),
            ).pack(anchor="w")
            value_label = tk.Label(
                card,
                textvariable=variable,
                background=PANEL,
                foreground=color,
                font=("Segoe UI Semibold", 13),
                wraplength=230,
                justify="left",
            )
            value_label.pack(anchor="w", pady=(5, 0))
            if title == "OVERALL":
                self.overall_value_label = value_label

    def _build_results(self, parent: ttk.Frame) -> None:
        results = ttk.Frame(parent, style="Panel.TFrame", padding=14)
        results.grid(row=4, column=0, sticky="nsew")
        results.columnconfigure(0, weight=1)
        results.rowconfigure(1, weight=3)
        results.rowconfigure(3, weight=1)

        ttk.Label(results, text="COMPONENT RESULTS", style="Section.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 9)
        )

        columns = ("component", "check", "target", "result", "latency", "diagnosis")
        self.tree = ttk.Treeview(
            results,
            columns=columns,
            show="headings",
            selectmode="browse",
        )
        headings = {
            "component": "COMPONENT",
            "check": "CHECK",
            "target": "TARGET",
            "result": "RESULT",
            "latency": "LATENCY",
            "diagnosis": "DIAGNOSIS",
        }
        widths = {
            "component": (190, True),
            "check": (78, False),
            "target": (320, True),
            "result": (85, False),
            "latency": (100, False),
            "diagnosis": (115, False),
        }
        for column in columns:
            self.tree.heading(column, text=headings[column])
            width, stretch = widths[column]
            self.tree.column(column, width=width, minwidth=60, stretch=stretch)

        tree_scroll = ttk.Scrollbar(results, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scroll.set)
        self.tree.grid(row=1, column=0, sticky="nsew")
        tree_scroll.grid(row=1, column=1, sticky="ns")
        self.tree.tag_configure("healthy", foreground="#86efac")
        self.tree.tag_configure("root_cause", foreground="#fda4af")
        self.tree.tag_configure("impacted", foreground="#fde68a")
        self.tree.tag_configure("discovered", foreground="#67e8f9")
        self.tree.bind("<<TreeviewSelect>>", self._show_selected_details)

        ttk.Label(results, text="DETAILS", style="Section.TLabel").grid(
            row=2, column=0, sticky="w", pady=(13, 7)
        )
        self.details = tk.Text(
            results,
            height=8,
            background="#091824",
            foreground="#cbdce7",
            insertbackground=TEXT,
            selectbackground="#164e63",
            relief="flat",
            padx=12,
            pady=10,
            wrap="word",
            font=("Consolas", 9),
            state="disabled",
        )
        details_scroll = ttk.Scrollbar(
            results, orient="vertical", command=self.details.yview
        )
        self.details.configure(yscrollcommand=details_scroll.set)
        self.details.grid(row=3, column=0, sticky="nsew")
        details_scroll.grid(row=3, column=1, sticky="ns")
        self._set_details(
            "Run an analysis and select a component to inspect its message, "
            "dependencies, and raw check details."
        )

    def _build_footer(self, parent: ttk.Frame) -> None:
        footer = ttk.Frame(parent)
        footer.grid(row=5, column=0, sticky="ew", pady=(12, 0))
        footer.columnconfigure(1, weight=1)

        export = ttk.Frame(footer)
        export.grid(row=0, column=0, sticky="w")
        self.view_topology_button = ttk.Button(
            export,
            text="View topology",
            style="Accent.TButton",
            command=self.open_topology,
            state="disabled",
        )
        self.view_topology_button.pack(side="left", padx=(0, 7))
        self.export_json_button = ttk.Button(
            export,
            text="Export JSON",
            style="Secondary.TButton",
            command=self.export_json,
            state="disabled",
        )
        self.export_json_button.pack(side="left", padx=(0, 7))
        self.export_mermaid_button = ttk.Button(
            export,
            text="Export Mermaid",
            style="Secondary.TButton",
            command=lambda: self.export_graph("mermaid"),
            state="disabled",
        )
        self.export_mermaid_button.pack(side="left", padx=(0, 7))
        self.export_dot_button = ttk.Button(
            export,
            text="Export DOT",
            style="Secondary.TButton",
            command=lambda: self.export_graph("dot"),
            state="disabled",
        )
        self.export_dot_button.pack(side="left")

        status_frame = ttk.Frame(footer)
        status_frame.grid(row=0, column=1, sticky="e")
        ttk.Label(status_frame, textvariable=self.status, style="Muted.TLabel").pack(
            side="left", padx=(12, 12)
        )
        self.progress = ttk.Progressbar(status_frame, mode="indeterminate", length=120)
        self.progress.pack(side="left")

    def _bind_shortcuts(self) -> None:
        self.root.bind("<Control-o>", lambda _event: self.browse())
        self.root.bind("<Control-d>", lambda _event: self.discover())
        self.root.bind("<Control-m>", lambda _event: self.open_topology())
        self.root.bind("<F5>", lambda _event: self.run())
        self.root.bind("<Control-s>", lambda _event: self.export_json())

    def update_or_check(self) -> None:
        """Install an available update or perform a new manual check."""

        if self.update_installed:
            self.restart_gui()
        elif self.latest_update and self.latest_update.update_available:
            self.install_available_update()
        else:
            self.check_updates(silent=False)

    def check_updates(self, *, silent: bool = False) -> None:
        """Check the HTTPS version manifest without blocking the GUI thread."""

        if (
            self.update_check_running
            or self.update_install_running
            or self.update_installed
        ):
            return
        self.update_check_running = True
        self.update_button.configure(
            text="Checking…",
            style="Secondary.TButton",
            state="disabled",
        )

        def worker() -> None:
            try:
                info = fetch_update_info(__version__)
                self.events.put(("update_info", (info, silent)))
            except Exception as exc:
                self.events.put(
                    (
                        "update_check_error",
                        (f"{type(exc).__name__}: {exc}", silent),
                    )
                )

        threading.Thread(
            target=worker,
            name="sdmap-gui-update-check",
            daemon=True,
        ).start()

    def _show_update_info(self, info: UpdateInfo, silent: bool) -> None:
        self.update_check_running = False
        self.latest_update = info
        state = "disabled" if self.busy else "normal"
        if info.update_available:
            self.version_badge.configure(foreground=YELLOW)
            self.update_button.configure(
                text=(f"Update v{info.current_version} → v{info.latest_version}"),
                style="Accent.TButton",
                state=state,
            )
            if not silent:
                self.install_available_update()
            return

        if info.current_is_newer:
            self.version_badge.configure(foreground=YELLOW)
            self.update_button.configure(
                text=(f"Feed v{info.latest_version} / local v{info.current_version}"),
                style="Secondary.TButton",
                state=state,
            )
            if not silent:
                messagebox.showwarning(
                    "Update feed is older",
                    (
                        f"Installed: v{info.current_version}\n"
                        f"Update feed: v{info.latest_version}\n\n"
                        "The program cannot confirm that it is up to date. "
                        "Try checking again."
                    ),
                    parent=self.root,
                )
            return

        self.version_badge.configure(foreground=GREEN)
        self.update_button.configure(
            text=f"v{info.current_version} is latest ✓",
            style="Secondary.TButton",
            state=state,
        )
        if not silent:
            messagebox.showinfo(
                "No updates available",
                (
                    f"Installed: v{info.current_version}\n"
                    f"Latest: v{info.latest_version}\n\n"
                    "The installed version matches the update feed."
                ),
                parent=self.root,
            )

    def _show_update_check_error(self, message: str, silent: bool) -> None:
        self.update_check_running = False
        self.update_button.configure(
            text="Check for updates",
            style="Secondary.TButton",
            state="disabled" if self.busy else "normal",
        )
        if not silent:
            messagebox.showerror(
                "Cannot check for updates",
                message,
                parent=self.root,
            )

    def install_available_update(self) -> None:
        """Install a discovered update after explicit user confirmation."""

        info = self.latest_update
        if info is None or not info.update_available:
            self.check_updates(silent=False)
            return
        if self.busy or self.update_install_running:
            return

        summary = f"\n\n{info.summary}" if info.summary else ""
        install_prompt = (
            "Download and open the Windows installer now?"
            if getattr(sys, "frozen", False)
            else "Install it now in the current Python environment?"
        )
        confirmed = messagebox.askyesno(
            "Install update",
            (
                f"Version {info.latest_version} is available "
                f"(installed: {info.current_version}).{summary}\n\n"
                f"{install_prompt}"
            ),
            parent=self.root,
        )
        if not confirmed:
            return

        self.update_install_running = True
        self._set_busy(True)
        self.update_button.configure(text="Installing update…", state="disabled")
        self.status.set(f"Installing version {info.latest_version}…")

        def worker() -> None:
            try:
                result = install_update(
                    latest_version=info.latest_version,
                    installer_url=info.installer_url,
                )
                self.events.put(("update_installed", result))
            except Exception as exc:
                self.events.put(
                    (
                        "update_install_error",
                        f"{type(exc).__name__}: {exc}",
                    )
                )

        threading.Thread(
            target=worker,
            name="sdmap-gui-update-install",
            daemon=True,
        ).start()

    def _show_update_installed(self, result: UpdateResult) -> None:
        self.update_install_running = False
        self._set_busy(False)
        latest = self.latest_update.latest_version if self.latest_update else "latest"
        if result.method == "Windows installer":
            self.status.set(f"Windows installer for version {latest} was opened.")
            messagebox.showinfo(
                "Installer opened",
                (
                    f"The installer for version {latest} is ready. "
                    "Complete the setup wizard to update the application.\n\n"
                    "Service Dependency Mapper will now close."
                ),
                parent=self.root,
            )
            self._close()
            return

        self.update_installed = True
        self.version_badge.configure(text=f"v{latest}", foreground=GREEN)
        self.update_button.configure(
            text="Restart now",
            style="Accent.TButton",
            command=self.restart_gui,
            state="normal",
        )
        self.status.set(
            f"Version {latest} installed using {result.method}. Restart required."
        )
        if messagebox.askyesno(
            "Update installed",
            f"Version {latest} was installed successfully.\n\nRestart the GUI now?",
            parent=self.root,
        ):
            self.restart_gui()

    def _show_update_install_error(self, message: str) -> None:
        self.update_install_running = False
        self._set_busy(False)
        latest = self.latest_update.latest_version if self.latest_update else "latest"
        self.update_button.configure(
            text=f"Update to v{latest}",
            style="Accent.TButton",
            command=self.update_or_check,
            state="normal",
        )
        self.status.set("Automatic update did not finish; review the error details.")
        messagebox.showerror(
            "Update failed safely",
            message,
            parent=self.root,
        )

    def restart_gui(self) -> None:
        """Start the updated GUI in the same interpreter and close this one."""

        command = (
            [sys.executable]
            if getattr(sys, "frozen", False)
            else [sys.executable, "-m", "service_dependency_mapper", "gui"]
        )
        selected = self.config_path.get().strip()
        if selected:
            command.append(selected)
        try:
            subprocess.Popen(command, cwd=str(Path.cwd()))
        except OSError as exc:
            messagebox.showerror(
                "Cannot restart",
                str(exc),
                parent=self.root,
            )
            return
        self._close()

    def browse(self) -> None:
        current = Path(self.config_path.get()).expanduser()
        initial_directory = current.parent if current.parent.exists() else Path.cwd()
        selected = filedialog.askopenfilename(
            title="Select a service map",
            initialdir=initial_directory,
            filetypes=(("YAML service maps", "*.yaml *.yml"), ("All files", "*.*")),
        )
        if selected:
            self.config_path.set(selected)
            self.current_map = None
            self.current_report = None
            self.current_discovery = None
            self.discovery_path = None
            self._enable_graph_exports(False)
            self.status.set("Configuration selected. Ready to validate.")

    def create_template(self) -> None:
        destination = filedialog.asksaveasfilename(
            title="Create a service map template",
            defaultextension=".yaml",
            filetypes=(("YAML service map", "*.yaml"), ("All files", "*.*")),
            initialfile="my-service.yaml",
        )
        if not destination:
            return
        try:
            Path(destination).write_text(DEFAULT_TEMPLATE, encoding="utf-8")
        except OSError as exc:
            messagebox.showerror(
                "Cannot create template",
                str(exc),
                parent=self.root,
            )
            return
        self.config_path.set(destination)
        self.current_map = None
        self.current_report = None
        self.current_discovery = None
        self.discovery_path = None
        self._enable_graph_exports(False)
        self.status.set("New service map template created. Ready to validate.")

    def discover(self) -> None:
        """Discover connected private networks and open the generated topology."""

        if self.busy:
            return
        try:
            timeout = parse_timeout(self.timeout.get()) or 0.3
            workers = parse_workers(
                self.workers.get(),
                workload="discovery",
            )
            settings = DiscoverySettings(
                timeout=timeout,
                workers=(
                    workers
                    if workers is not None
                    else automatic_worker_count("discovery")
                ),
            )
            additional_targets = parse_discovery_targets(self.discovery_targets.get())
        except (DiscoveryError, ValueError) as exc:
            self.status.set("Cannot start infrastructure discovery.")
            messagebox.showerror(
                "Invalid discovery settings",
                str(exc),
                parent=self.root,
            )
            return
        worker_plan = build_discovery_worker_plan(settings.workers)
        self.discovery_cancel.clear()
        self.current_report = None
        self.current_discovery = None
        self.results_by_id = {}
        self._set_busy(True)
        self.discover_button.configure(
            text="Cancel discovery",
            command=self.cancel_discovery,
            state="normal",
        )
        self.overall.set("DISCOVERING")
        self.root_causes.set("—")
        self.component_count.set("0")
        self.duration.set("—")
        self.overall_value_label.configure(foreground=CYAN)
        self.status.set(
            f"Preparing {worker_plan.tcp_workers} I/O workers across "
            f"{worker_plan.logical_processors} logical processors…"
        )

        def progress(stage: str, completed: int, total: int, message: str) -> None:
            self.events.put(
                (
                    "discovery_progress",
                    {
                        "stage": stage,
                        "completed": completed,
                        "total": total,
                        "message": message,
                    },
                )
            )

        def worker() -> None:
            try:
                result = discover_infrastructure(
                    additional_targets=additional_targets,
                    settings=settings,
                    progress=progress,
                    cancel_event=self.discovery_cancel,
                )
                destination = write_discovery_map(
                    result,
                    default_discovery_output(),
                )
                dependency_map = load_config(destination)
                self.events.put(("discovery", (result, destination, dependency_map)))
            except (DiscoveryError, ConfigError, OSError, ValueError) as exc:
                event = (
                    "discovery_cancelled"
                    if self.discovery_cancel.is_set()
                    else "discovery_error"
                )
                self.events.put((event, str(exc)))
            except Exception as exc:
                self.events.put(
                    (
                        "discovery_error",
                        f"{type(exc).__name__}: {exc}",
                    )
                )

        threading.Thread(
            target=worker,
            name="sdmap-gui-discovery",
            daemon=True,
        ).start()

    def cancel_discovery(self) -> None:
        if not self.busy:
            return
        self.discovery_cancel.set()
        self.discover_button.configure(state="disabled")
        self.status.set("Cancelling infrastructure discovery…")

    def _load_map(self) -> DependencyMap:
        path_value = self.config_path.get().strip()
        if not path_value:
            raise ConfigError("Select a YAML configuration file first.")
        timeout = parse_timeout(self.timeout.get())
        workers = parse_workers(self.workers.get())
        return load_config(
            Path(path_value).expanduser(),
            timeout_override=timeout,
            workers_override=workers,
        )

    def _selected_discovery_is_current(self) -> bool:
        if self.discovery_path is None:
            return False
        try:
            selected = Path(self.config_path.get()).expanduser().resolve()
        except OSError:
            return False
        return selected == self.discovery_path

    def validate(self) -> None:
        if self.busy:
            return
        try:
            dependency_map = self._load_map()
        except (ConfigError, ValueError) as exc:
            self.status.set("Configuration validation failed.")
            messagebox.showerror("Invalid configuration", str(exc), parent=self.root)
            return

        self.current_map = dependency_map
        if not self._selected_discovery_is_current():
            self.current_discovery = None
            self.discovery_path = None
        self._enable_graph_exports(True)
        order = " → ".join(topological_order(dependency_map))
        self.status.set(
            f"Valid configuration: {len(dependency_map.components)} components."
        )
        messagebox.showinfo(
            "Configuration valid",
            f"{dependency_map.service_name}\n\nDependency-first order:\n{order}",
            parent=self.root,
        )

    def run(self) -> None:
        if self.busy:
            return
        try:
            dependency_map = self._load_map()
        except (ConfigError, ValueError) as exc:
            self.status.set("Cannot start analysis.")
            messagebox.showerror("Cannot run analysis", str(exc), parent=self.root)
            return

        self.current_map = dependency_map
        self.current_report = None
        if not self._selected_discovery_is_current():
            self.current_discovery = None
            self.discovery_path = None
        self._set_busy(True)
        self.status.set(f"Checking {len(dependency_map.components)} components…")

        def worker() -> None:
            try:
                report = analyze(dependency_map)
                self.events.put(("report", report))
            except Exception as exc:
                self.events.put(("error", f"{type(exc).__name__}: {exc}"))

        threading.Thread(
            target=worker,
            name="sdmap-gui-analysis",
            daemon=True,
        ).start()

    def _poll_events(self) -> None:
        if self.closed:
            return
        try:
            while True:
                event, payload = self.events.get_nowait()
                if event == "report":
                    self._show_report(payload)
                elif event == "error":
                    self._show_analysis_error(str(payload))
                elif event == "discovery_progress":
                    self._show_discovery_progress(payload)
                elif event == "discovery":
                    self._show_discovery(payload)
                elif event == "discovery_error":
                    self._show_discovery_error(str(payload))
                elif event == "discovery_cancelled":
                    self._show_discovery_cancelled()
                elif event == "update_info":
                    info, silent = payload
                    self._show_update_info(info, bool(silent))
                elif event == "update_check_error":
                    message, silent = payload
                    self._show_update_check_error(str(message), bool(silent))
                elif event == "update_installed":
                    self._show_update_installed(payload)
                elif event == "update_install_error":
                    self._show_update_install_error(str(payload))
        except queue.Empty:
            pass
        self.root.after(100, self._poll_events)

    def _show_discovery_progress(self, payload: dict[str, Any]) -> None:
        if not self.busy:
            return
        completed = int(payload.get("completed", 0))
        total = int(payload.get("total", 0))
        message = str(payload.get("message", "Discovering infrastructure…"))
        suffix = f" ({completed}/{total})" if total else ""
        self.status.set(f"{message}{suffix}")

    def _show_discovery(
        self,
        payload: tuple[DiscoveryResult, Path, DependencyMap],
    ) -> None:
        result, destination, dependency_map = payload
        self._set_busy(False)
        self.current_discovery = result
        self.discovery_path = destination.resolve()
        self.current_map = dependency_map
        self.current_report = None
        self.results_by_id = {}
        self.config_path.set(str(destination))

        self.tree.delete(*self.tree.get_children())
        for component in dependency_map.components:
            category = "COMPONENT"
            if "network" in component.tags:
                category = "NETWORK"
            elif "host" in component.tags:
                category = "HOST"
            elif "service" in component.tags:
                category = "SERVICE"
            self.tree.insert(
                "",
                "end",
                iid=component.component_id,
                values=(
                    component.name,
                    component.check_type.upper(),
                    component.target,
                    "FOUND",
                    "—",
                    category,
                ),
                tags=("discovered",),
            )

        self.overall.set("DISCOVERED")
        self.overall_value_label.configure(foreground=CYAN)
        self.root_causes.set("—")
        self.component_count.set(str(len(dependency_map.components)))
        self.duration.set(f"{result.duration_ms:.2f} ms")
        warning = f" {len(result.warnings)} safety note(s)." if result.warnings else ""
        self.status.set(
            f"Discovery complete: {len(result.hosts)} host(s), "
            f"{result.service_count} service(s). "
            f"{result.worker_count} workers / "
            f"{result.logical_processors} logical CPUs. "
            f"Map saved as {destination.name}.{warning}"
        )
        self._enable_graph_exports(True)

        first = self.tree.get_children()
        if first:
            self.tree.selection_set(first[0])
            self.tree.focus(first[0])
            self._show_selected_details()
        self.open_topology()

    def _show_discovery_error(self, message: str) -> None:
        self._set_busy(False)
        self.overall.set("NOT RUN")
        self.overall_value_label.configure(foreground=CYAN)
        self.status.set("Infrastructure discovery failed.")
        messagebox.showerror(
            "Discovery failed",
            message,
            parent=self.root,
        )

    def _show_discovery_cancelled(self) -> None:
        self._set_busy(False)
        self.overall.set("NOT RUN")
        self.overall_value_label.configure(foreground=CYAN)
        self.status.set("Infrastructure discovery cancelled.")

    def _show_report(self, report: AnalysisReport) -> None:
        self._set_busy(False)
        self.current_report = report
        self.results_by_id = {
            item.component.component_id: item for item in report.results
        }
        self.tree.delete(*self.tree.get_children())
        for item in report.results:
            self.tree.insert(
                "",
                "end",
                iid=item.component.component_id,
                values=result_row(item),
                tags=(item.diagnosis.value,),
            )

        summary = report_summary(report)
        self.overall.set(summary["overall"])
        overall_color = {
            "HEALTHY": GREEN,
            "DEGRADED": YELLOW,
            "DOWN": RED,
        }[summary["overall"]]
        self.overall_value_label.configure(foreground=overall_color)
        self.root_causes.set(summary["root_causes"])
        self.component_count.set(summary["components"])
        self.duration.set(summary["duration"])
        self.status.set(
            f"Analysis complete: {report.overall_status.value.upper()} "
            f"in {report.duration_ms:.2f} ms."
        )
        self.export_json_button.configure(state="normal")
        self._enable_graph_exports(True)

        first = self.tree.get_children()
        if first:
            self.tree.selection_set(first[0])
            self.tree.focus(first[0])
            self._show_selected_details()

    def _show_analysis_error(self, message: str) -> None:
        self._set_busy(False)
        self._enable_graph_exports(True)
        self.status.set("Analysis failed.")
        messagebox.showerror("Analysis failed", message, parent=self.root)

    def _show_selected_details(self, _event: object | None = None) -> None:
        selected = self.tree.selection()
        if not selected:
            return
        item = self.results_by_id.get(selected[0])
        if item is None:
            if self.current_map is None:
                return
            component = self.current_map.components_by_id.get(selected[0])
            if component is None:
                return
            details = [
                f"Component:  {component.name} ({component.component_id})",
                f"Check:      {component.check_type.upper()}",
                f"Target:     {component.target}",
                f"Depends on: {', '.join(component.depends_on) or 'none'}",
                f"Critical:   {'yes' if component.critical else 'no'}",
                f"Tags:       {', '.join(component.tags) or 'none'}",
            ]
            inventory = discovery_detail_lines(
                self.current_discovery,
                component.component_id,
            )
            if inventory:
                details.extend(["", *inventory])
            details.extend(
                [
                    "",
                    "State: discovered; run analysis for live health diagnosis.",
                ]
            )
            self._set_details("\n".join(details))
            return
        details = [
            (
                f"Component:           {item.component.name} "
                f"({item.component.component_id})"
            ),
            f"Check:               {item.component.check_type.upper()}",
            f"Target:              {item.component.target}",
            f"Critical:            {'yes' if item.component.critical else 'no'}",
            f"Raw result:          {item.result.status.value.upper()}",
            f"Diagnosis:           {item.diagnosis.value.replace('_', ' ').upper()}",
            f"Depends on:          {', '.join(item.component.depends_on) or 'none'}",
            (f"Failed dependencies: {', '.join(item.failed_dependencies) or 'none'}"),
            f"Message:             {item.result.message}",
        ]
        inventory = discovery_detail_lines(
            self.current_discovery,
            item.component.component_id,
        )
        if inventory:
            details.extend(["", "Discovery inventory:", *inventory])
        if item.result.details:
            details.extend(
                [
                    "",
                    "Raw details:",
                    json.dumps(item.result.details, indent=2, ensure_ascii=False),
                ]
            )
        self._set_details("\n".join(details))

    def _set_details(self, content: str) -> None:
        self.details.configure(state="normal")
        self.details.delete("1.0", "end")
        self.details.insert("1.0", content)
        self.details.configure(state="disabled")

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.browse_button.configure(state=state)
        self.template_button.configure(state=state)
        self.discover_button.configure(
            text="Discover infrastructure",
            command=self.discover,
            state=state,
        )
        self.validate_button.configure(state=state)
        self.run_button.configure(state=state)
        self.path_entry.configure(state=state)
        self.target_entry.configure(state=state)
        update_state = (
            "disabled"
            if busy or self.update_check_running or self.update_install_running
            else "normal"
        )
        self.update_button.configure(state=update_state)
        if busy:
            self.progress.start(12)
            self.export_json_button.configure(state="disabled")
            self._enable_graph_exports(False)
        else:
            self.progress.stop()
            self._enable_graph_exports(True)

    def _enable_graph_exports(self, enabled: bool) -> None:
        state = "normal" if enabled and self.current_map is not None else "disabled"
        self.export_mermaid_button.configure(state=state)
        self.export_dot_button.configure(state=state)
        self.view_topology_button.configure(state=state)

    def open_topology(self) -> None:
        if self.busy:
            return
        if self.current_map is None:
            messagebox.showinfo(
                "No topology available",
                "Discover infrastructure or validate a YAML map first.",
                parent=self.root,
            )
            return
        TopologyWindow(
            self.root,
            self.current_map,
            self.current_report,
            self.current_discovery,
        )

    def export_json(self) -> None:
        if self.current_report is None:
            if not self.busy:
                messagebox.showinfo(
                    "Nothing to export",
                    "Run an analysis before exporting JSON.",
                    parent=self.root,
                )
            return
        destination = filedialog.asksaveasfilename(
            title="Export analysis report",
            defaultextension=".json",
            filetypes=(("JSON report", "*.json"), ("All files", "*.*")),
            initialfile="service-report.json",
        )
        if destination:
            self._save_text(
                Path(destination),
                render_json(self.current_report),
                "JSON report exported.",
            )

    def export_graph(self, graph_format: str) -> None:
        if self.current_map is None:
            return
        is_mermaid = graph_format == "mermaid"
        extension = ".mmd" if is_mermaid else ".dot"
        destination = filedialog.asksaveasfilename(
            title=f"Export {graph_format.title()} graph",
            defaultextension=extension,
            filetypes=(
                (
                    f"{graph_format.title()} graph",
                    f"*{extension}",
                ),
                ("All files", "*.*"),
            ),
            initialfile=f"service-map{extension}",
        )
        if not destination:
            return
        content = (
            render_mermaid(self.current_map)
            if is_mermaid
            else render_dot(self.current_map)
        )
        self._save_text(
            Path(destination),
            content,
            f"{graph_format.title()} graph exported.",
        )

    def _save_text(self, destination: Path, content: str, success: str) -> None:
        try:
            destination.write_text(content, encoding="utf-8")
        except OSError as exc:
            messagebox.showerror("Export failed", str(exc), parent=self.root)
            return
        self.status.set(f"{success} {destination}")

    def _close(self) -> None:
        if self.update_install_running:
            messagebox.showinfo(
                "Update in progress",
                "Wait for the update to finish before closing the application.",
                parent=self.root,
            )
            return
        self.closed = True
        self.discovery_cancel.set()
        self.root.destroy()


def launch_gui(config_path: str | Path | None = None) -> int:
    """Create and run the desktop interface."""

    try:
        root = tk.Tk()
    except tk.TclError as exc:
        raise GuiUnavailableError(
            "The graphical interface could not start. "
            "Run it from a desktop session with Tk support."
        ) from exc
    ServiceDependencyMapperGui(root, config_path)
    root.mainloop()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Console entry point for ``sdmap-gui``."""

    parser = argparse.ArgumentParser(
        prog="sdmap-gui",
        description="Launch the Service Dependency Mapper desktop interface.",
    )
    parser.add_argument(
        "config",
        nargs="?",
        type=Path,
        help="Optional YAML service map to preselect.",
    )
    args = parser.parse_args(argv)
    try:
        return launch_gui(args.config)
    except GuiUnavailableError as exc:
        print(f"GUI error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
