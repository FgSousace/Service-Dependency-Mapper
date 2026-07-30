"""Dependency graph exporters."""

from __future__ import annotations

from service_dependency_mapper.models import DependencyMap


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def render_mermaid(dependency_map: DependencyMap) -> str:
    """Render the service map as a Mermaid flowchart."""

    lines = ["flowchart LR"]
    for component in dependency_map.components:
        label = _escape_label(f"{component.name}\\n{component.check_type.upper()}")
        lines.append(f'    {component.component_id}["{label}"]')
    for component in dependency_map.components:
        for dependency_id in component.depends_on:
            lines.append(f"    {dependency_id} --> {component.component_id}")
    return "\n".join(lines) + "\n"


def render_dot(dependency_map: DependencyMap) -> str:
    """Render the service map in Graphviz DOT syntax."""

    lines = [
        "digraph service_dependencies {",
        "    rankdir=LR;",
        '    node [shape=box, style="rounded"];',
    ]
    for component in dependency_map.components:
        label = _escape_label(f"{component.name}\\n{component.check_type.upper()}")
        lines.append(f'    {component.component_id} [label="{label}"];')
    for component in dependency_map.components:
        for dependency_id in component.depends_on:
            lines.append(f"    {dependency_id} -> {component.component_id};")
    lines.append("}")
    return "\n".join(lines) + "\n"
