# Changelog

Wszystkie istotne zmiany w projekcie są dokumentowane w tym pliku.

## [1.2.0] - 2026-07-30

### Added

- one-click, vendor-neutral discovery of connected private IPv4 networks,
- automatic Windows and Linux interface and gateway detection,
- concurrent ICMP, ARP/neighbor-cache, reverse-DNS, and TCP discovery,
- broad scanning of TCP ports 1-1024 plus common infrastructure high ports,
- safe HTTP/TLS and text-banner fingerprinting without credentials,
- generated YAML inventory with networks, hosts, MAC addresses, names, ports,
  protocols, banners, and discovery metadata,
- automatic `network -> host -> service` dependency generation,
- interactive topology window with zoom, fit, panning, node inspection, and
  health-state coloring,
- `sdmap discover` terminal command,
- topology-only logical nodes using `check.type: none`,
- cancellable background discovery that keeps the desktop interface responsive,
- discovery and topology test coverage, increasing the suite to 65 tests.

### Changed

- updated the desktop interface, documentation, banner, and previews for
  infrastructure discovery,
- removed the product-specific laboratory example so the repository stays
  fully vendor-neutral.

## [1.1.0] - 2026-07-30

### Added

- vendor-neutral desktop GUI built with Tkinter,
- configuration picker and new service-map template,
- background analysis without freezing the window,
- colored component table and detailed diagnosis panel,
- JSON, Mermaid, and Graphviz export from the GUI,
- `sdmap gui` and `sdmap-gui` launch commands,
- ICMP reachability checks,
- TLS certificate, hostname, handshake, and expiry checks,
- generic mixed-infrastructure example,
- GUI and new check coverage, increasing the suite to 53 tests.

### Changed

- clarified that the mapper is not coupled to any monitoring vendor,
- expanded documentation and visual examples for desktop usage.

## [1.0.0] - 2026-07-30

### Added

- YAML dependency maps,
- concurrent DNS, TCP, and HTTP checks,
- root-cause and downstream-impact analysis,
- terminal and JSON reports,
- Mermaid and Graphviz graph export,
- configuration validation and GitHub Actions.
