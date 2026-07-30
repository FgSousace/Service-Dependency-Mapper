"""Layout helpers for the interactive desktop topology view."""

from __future__ import annotations

import math
from dataclasses import dataclass

from service_dependency_mapper.models import DependencyMap


@dataclass(frozen=True, slots=True)
class NodePosition:
    """Canvas geometry for one component node."""

    x: float
    y: float
    width: float
    height: float
    depth: int

    @property
    def center_x(self) -> float:
        return self.x + (self.width / 2)

    @property
    def top(self) -> float:
        return self.y

    @property
    def bottom(self) -> float:
        return self.y + self.height


@dataclass(frozen=True, slots=True)
class TopologyLayout:
    """Complete deterministic layout for a dependency map."""

    nodes: dict[str, NodePosition]
    width: float
    height: float
    layers: int


def component_depths(dependency_map: DependencyMap) -> dict[str, int]:
    """Return the longest dependency distance for every component."""

    cache: dict[str, int] = {}

    def depth(component_id: str) -> int:
        if component_id in cache:
            return cache[component_id]
        component = dependency_map.components_by_id[component_id]
        value = (
            0
            if not component.depends_on
            else max(depth(dependency) for dependency in component.depends_on) + 1
        )
        cache[component_id] = value
        return value

    for component in dependency_map.components:
        depth(component.component_id)
    return cache


def build_topology_layout(
    dependency_map: DependencyMap,
    *,
    max_columns: int = 6,
    node_width: float = 210,
    node_height: float = 76,
    horizontal_gap: float = 42,
    vertical_gap: float = 54,
    layer_gap: float = 92,
    margin: float = 70,
) -> TopologyLayout:
    """Lay out arbitrary DAG layers without requiring Graphviz."""

    if max_columns < 1:
        raise ValueError("max_columns must be at least one.")

    depths = component_depths(dependency_map)
    layer_count = max(depths.values(), default=0) + 1
    layers: dict[int, list[str]] = {depth: [] for depth in range(layer_count)}
    for component in dependency_map.components:
        layers[depths[component.component_id]].append(component.component_id)

    widest_columns = max(
        (min(len(component_ids), max_columns) for component_ids in layers.values()),
        default=1,
    )
    content_width = (widest_columns * node_width) + (
        max(0, widest_columns - 1) * horizontal_gap
    )
    canvas_width = max(760.0, content_width + (margin * 2))

    nodes: dict[str, NodePosition] = {}
    y = margin
    for layer_index in range(layer_count):
        component_ids = layers[layer_index]
        rows = max(1, math.ceil(len(component_ids) / max_columns))
        for row in range(rows):
            row_ids = component_ids[row * max_columns : (row + 1) * max_columns]
            row_width = (len(row_ids) * node_width) + (
                max(0, len(row_ids) - 1) * horizontal_gap
            )
            start_x = (canvas_width - row_width) / 2
            for column, component_id in enumerate(row_ids):
                nodes[component_id] = NodePosition(
                    x=start_x + (column * (node_width + horizontal_gap)),
                    y=y + (row * (node_height + vertical_gap)),
                    width=node_width,
                    height=node_height,
                    depth=layer_index,
                )
        y += (rows * node_height) + (max(0, rows - 1) * vertical_gap) + layer_gap

    canvas_height = max(520.0, y - layer_gap + margin)
    return TopologyLayout(
        nodes=nodes,
        width=canvas_width,
        height=canvas_height,
        layers=layer_count,
    )
