# Changelog

Wszystkie istotne zmiany w projekcie są dokumentowane w tym pliku.

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
- dedicated optional Zabbix lab example,
- GUI and new check coverage, increasing the suite to 53 tests.

### Changed

- clarified that the mapper is not coupled to Zabbix or another monitoring
  vendor,
- expanded documentation and visual examples for desktop usage.

## [1.0.0] - 2026-07-30

### Added

- YAML dependency maps,
- concurrent DNS, TCP, and HTTP checks,
- root-cause and downstream-impact analysis,
- terminal and JSON reports,
- Mermaid and Graphviz graph export,
- configuration validation and GitHub Actions.
