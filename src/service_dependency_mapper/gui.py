"""Tkinter desktop interface for Service Dependency Mapper."""

from __future__ import annotations

import argparse
import json
import queue
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
from service_dependency_mapper.graph import render_dot, render_mermaid
from service_dependency_mapper.models import (
    AnalysisReport,
    AnalyzedResult,
    DependencyMap,
)
from service_dependency_mapper.reporting import render_json

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
  workers: 8

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


def parse_workers(value: str) -> int | None:
    """Parse an optional worker count accepted by the configuration loader."""

    value = value.strip()
    if not value:
        return None
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError("Workers must be an integer from 1 to 64.") from exc
    if not 1 <= parsed <= 64:
        raise ValueError("Workers must be an integer from 1 to 64.")
    return parsed


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


class ServiceDependencyMapperGui:
    """Responsive desktop UI backed by the existing analysis engine."""

    def __init__(self, root: tk.Tk, config_path: str | Path | None = None) -> None:
        self.root = root
        self.root.title(f"Service Dependency Mapper {__version__}")
        self.root.geometry("1240x820")
        self.root.minsize(960, 680)
        self.root.configure(background=BACKGROUND)

        self.current_map: DependencyMap | None = None
        self.current_report: AnalysisReport | None = None
        self.results_by_id: dict[str, AnalyzedResult] = {}
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.closed = False
        self.busy = False

        self.config_path = tk.StringVar(
            value=str(Path(config_path)) if config_path else ""
        )
        self.timeout = tk.StringVar()
        self.workers = tk.StringVar()
        self.status = tk.StringVar(
            value="Select a YAML service map, then validate or run the analysis."
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
        tk.Label(
            header,
            text=f"v{__version__}",
            background=PANEL_ALT,
            foreground=CYAN,
            padx=10,
            pady=5,
            font=("Consolas", 9, "bold"),
        ).grid(row=0, column=2, rowspan=2, sticky="e")

    def _build_controls(self, parent: ttk.Frame) -> None:
        panel = ttk.Frame(parent, style="Panel.TFrame", padding=16)
        panel.grid(row=1, column=0, sticky="ew", pady=(0, 14))
        panel.columnconfigure(0, weight=1)

        ttk.Label(panel, text="SERVICE MAP", style="Section.TLabel").grid(
            row=0, column=0, columnspan=5, sticky="w", pady=(0, 9)
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
        self.validate_button = ttk.Button(
            panel,
            text="Validate",
            style="Secondary.TButton",
            command=self.validate,
        )
        self.validate_button.grid(row=1, column=3, padx=(0, 8))
        self.run_button = ttk.Button(
            panel,
            text="Run analysis",
            style="Accent.TButton",
            command=self.run,
        )
        self.run_button.grid(row=1, column=4)

        options = tk.Frame(panel, background=PANEL)
        options.grid(row=2, column=0, columnspan=5, sticky="ew", pady=(13, 0))
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
            text="Workers:",
            background=PANEL,
            foreground=MUTED,
            font=("Segoe UI", 9),
        ).pack(side="left")
        ttk.Entry(options, textvariable=self.workers, width=8).pack(
            side="left", padx=(6, 0)
        )

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
        self.root.bind("<F5>", lambda _event: self.run())
        self.root.bind("<Control-s>", lambda _event: self.export_json())

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
        self.status.set("New service map template created. Ready to validate.")

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
        except queue.Empty:
            pass
        self.root.after(100, self._poll_events)

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
        self.validate_button.configure(state=state)
        self.run_button.configure(state=state)
        self.path_entry.configure(state=state)
        if busy:
            self.progress.start(12)
            self.export_json_button.configure(state="disabled")
            self._enable_graph_exports(False)
        else:
            self.progress.stop()

    def _enable_graph_exports(self, enabled: bool) -> None:
        state = "normal" if enabled and self.current_map is not None else "disabled"
        self.export_mermaid_button.configure(state=state)
        self.export_dot_button.configure(state=state)

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
        self.closed = True
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
