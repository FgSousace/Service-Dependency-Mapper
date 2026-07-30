"""YAML configuration loading and dependency-graph validation."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from service_dependency_mapper.models import Component, DependencyMap

_COMPONENT_ID = re.compile(r"^[a-z][a-z0-9_-]*$")
_CHECK_TYPES = {"dns", "http", "icmp", "none", "tcp", "tls"}


class ConfigError(ValueError):
    """Raised when a service map is missing data or has an invalid graph."""


def _mapping(value: Any, location: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{location} must be a mapping.")
    return value


def _positive_number(value: Any, location: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ConfigError(f"{location} must be a number greater than zero.")
    return float(value)


def _validate_check(check: dict[str, Any], location: str) -> str:
    check_type = check.get("type")
    if check_type not in _CHECK_TYPES:
        allowed = ", ".join(sorted(_CHECK_TYPES))
        raise ConfigError(f"{location}.type must be one of: {allowed}.")

    if check_type == "none":
        unexpected = set(check) - {"type", "timeout"}
        if unexpected:
            names = ", ".join(sorted(unexpected))
            raise ConfigError(
                f"{location} has unsupported fields for a topology-only node: {names}."
            )

    elif check_type == "dns":
        target = check.get("target")
        if not isinstance(target, str) or not target.strip():
            raise ConfigError(f"{location}.target must be a non-empty hostname.")
        expected = check.get("expected_addresses", [])
        if not isinstance(expected, list) or not all(
            isinstance(item, str) and item for item in expected
        ):
            raise ConfigError(
                f"{location}.expected_addresses must be a list of addresses."
            )

    elif check_type in {"tcp", "tls"}:
        host = check.get("host")
        port = check.get("port", 443 if check_type == "tls" else None)
        if not isinstance(host, str) or not host.strip():
            raise ConfigError(f"{location}.host must be a non-empty hostname.")
        if (
            isinstance(port, bool)
            or not isinstance(port, int)
            or not 1 <= port <= 65535
        ):
            raise ConfigError(f"{location}.port must be an integer from 1 to 65535.")
        if check_type == "tls":
            server_name = check.get("server_name")
            if server_name is not None and (
                not isinstance(server_name, str) or not server_name.strip()
            ):
                raise ConfigError(
                    f"{location}.server_name must be a non-empty hostname."
                )
            min_days = check.get("min_days_remaining", 14)
            if (
                isinstance(min_days, bool)
                or not isinstance(min_days, int)
                or not 0 <= min_days <= 3650
            ):
                raise ConfigError(
                    f"{location}.min_days_remaining must be an integer from 0 to 3650."
                )

    elif check_type == "icmp":
        target = check.get("target")
        if not isinstance(target, str) or not target.strip():
            raise ConfigError(f"{location}.target must be a non-empty host or IP.")
        count = check.get("count", 1)
        if (
            isinstance(count, bool)
            or not isinstance(count, int)
            or not 1 <= count <= 10
        ):
            raise ConfigError(f"{location}.count must be an integer from 1 to 10.")

    elif check_type == "http":
        url = check.get("url")
        if not isinstance(url, str) or not url.strip():
            raise ConfigError(f"{location}.url must be a non-empty URL.")
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ConfigError(f"{location}.url must use http:// or https://.")

        expected_status = check.get("expected_status", [200])
        if not isinstance(expected_status, list) or not expected_status:
            raise ConfigError(f"{location}.expected_status must be a non-empty list.")
        if any(
            isinstance(status, bool)
            or not isinstance(status, int)
            or not 100 <= status <= 599
            for status in expected_status
        ):
            raise ConfigError(
                f"{location}.expected_status values must be HTTP codes from 100 to 599."
            )

        contains = check.get("contains")
        if contains is not None and not isinstance(contains, str):
            raise ConfigError(f"{location}.contains must be a string.")

        method = check.get("method", "GET")
        if method not in {"GET", "HEAD"}:
            raise ConfigError(f"{location}.method must be GET or HEAD.")

    return check_type


def _validate_graph(components: tuple[Component, ...]) -> None:
    known_ids = {component.component_id for component in components}
    for component in components:
        missing = set(component.depends_on) - known_ids
        if missing:
            names = ", ".join(sorted(missing))
            raise ConfigError(
                f"Component '{component.component_id}' has unknown "
                f"dependencies: {names}."
            )
        if component.component_id in component.depends_on:
            raise ConfigError(
                f"Component '{component.component_id}' cannot depend on itself."
            )

    dependencies = {
        component.component_id: component.depends_on for component in components
    }
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(component_id: str) -> None:
        current_state = state.get(component_id, 0)
        if current_state == 2:
            return
        if current_state == 1:
            cycle_start = stack.index(component_id)
            cycle = stack[cycle_start:] + [component_id]
            raise ConfigError(f"Dependency cycle detected: {' -> '.join(cycle)}.")

        state[component_id] = 1
        stack.append(component_id)
        for dependency_id in dependencies[component_id]:
            visit(dependency_id)
        stack.pop()
        state[component_id] = 2

    for component in components:
        visit(component.component_id)


def load_config(
    path: str | Path,
    *,
    timeout_override: float | None = None,
    workers_override: int | None = None,
) -> DependencyMap:
    """Load and validate a version 1 service map."""

    config_path = Path(path)
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"Configuration file not found: {config_path}") from exc
    except OSError as exc:
        raise ConfigError(f"Cannot read configuration file: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML: {exc}") from exc

    root = _mapping(raw, "Configuration")
    if root.get("version") != 1:
        raise ConfigError("Configuration version must be 1.")

    service = _mapping(root.get("service"), "service")
    service_name = service.get("name")
    if not isinstance(service_name, str) or not service_name.strip():
        raise ConfigError("service.name must be a non-empty string.")
    description = service.get("description", "")
    if not isinstance(description, str):
        raise ConfigError("service.description must be a string.")

    defaults = _mapping(root.get("defaults", {}), "defaults")
    default_timeout = _positive_number(defaults.get("timeout", 3), "defaults.timeout")
    workers = defaults.get("workers", 8)
    if (
        isinstance(workers, bool)
        or not isinstance(workers, int)
        or not 1 <= workers <= 64
    ):
        raise ConfigError("defaults.workers must be an integer from 1 to 64.")

    if timeout_override is not None:
        default_timeout = _positive_number(timeout_override, "--timeout")
    if workers_override is not None:
        if not 1 <= workers_override <= 64:
            raise ConfigError("--workers must be an integer from 1 to 64.")
        workers = workers_override

    raw_components = root.get("components")
    if not isinstance(raw_components, list) or not raw_components:
        raise ConfigError("components must be a non-empty list.")

    components: list[Component] = []
    seen_ids: set[str] = set()
    for index, raw_component in enumerate(raw_components):
        location = f"components[{index}]"
        component_data = _mapping(raw_component, location)
        component_id = component_data.get("id")
        if not isinstance(component_id, str) or not _COMPONENT_ID.fullmatch(
            component_id
        ):
            raise ConfigError(f"{location}.id must match {_COMPONENT_ID.pattern!r}.")
        if component_id in seen_ids:
            raise ConfigError(f"Duplicate component id: '{component_id}'.")
        seen_ids.add(component_id)

        name = component_data.get("name", component_id)
        if not isinstance(name, str) or not name.strip():
            raise ConfigError(f"{location}.name must be a non-empty string.")

        check = _mapping(component_data.get("check"), f"{location}.check")
        check_type = _validate_check(check, f"{location}.check")

        depends_on = component_data.get("depends_on", [])
        if not isinstance(depends_on, list) or not all(
            isinstance(item, str) for item in depends_on
        ):
            raise ConfigError(f"{location}.depends_on must be a list of ids.")
        if len(depends_on) != len(set(depends_on)):
            raise ConfigError(f"{location}.depends_on contains duplicate ids.")

        critical = component_data.get("critical", True)
        if not isinstance(critical, bool):
            raise ConfigError(f"{location}.critical must be true or false.")

        tags = component_data.get("tags", [])
        if not isinstance(tags, list) or not all(
            isinstance(item, str) for item in tags
        ):
            raise ConfigError(f"{location}.tags must be a list of strings.")

        component_timeout = check.get("timeout", default_timeout)
        component_timeout = _positive_number(
            component_timeout, f"{location}.check.timeout"
        )
        check_copy = dict(check)
        check_copy.pop("type", None)
        check_copy.pop("timeout", None)
        if check_type == "tls":
            check_copy.setdefault("port", 443)
            check_copy.setdefault("min_days_remaining", 14)
        elif check_type == "icmp":
            check_copy.setdefault("count", 1)

        components.append(
            Component(
                component_id=component_id,
                name=name.strip(),
                check_type=check_type,
                check=check_copy,
                depends_on=tuple(depends_on),
                critical=critical,
                timeout=component_timeout,
                tags=tuple(tags),
            )
        )

    component_tuple = tuple(components)
    _validate_graph(component_tuple)
    return DependencyMap(
        service_name=service_name.strip(),
        description=description.strip(),
        default_timeout=default_timeout,
        workers=workers,
        components=component_tuple,
    )


def topological_order(dependency_map: DependencyMap) -> tuple[str, ...]:
    """Return dependency-first component ids for a validated map."""

    ordered: list[str] = []
    visited: set[str] = set()

    def visit(component_id: str) -> None:
        if component_id in visited:
            return
        component = dependency_map.components_by_id[component_id]
        for dependency_id in component.depends_on:
            visit(dependency_id)
        visited.add(component_id)
        ordered.append(component_id)

    for component in dependency_map.components:
        visit(component.component_id)
    return tuple(ordered)
