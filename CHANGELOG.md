# Changelog

Wszystkie istotne zmiany w projekcie są dokumentowane w tym pliku.

## [1.6.0] - 2026-08-14

### Added

- self-contained Windows desktop application built with PyInstaller,
- installable Inno Setup `.exe` with Start menu and optional desktop shortcuts,
- per-user installation that does not require Python, Git, or administrator access,
- standard Windows uninstaller and post-install GUI launch,
- GitHub Actions workflow that builds, verifies, and uploads the installer,
- automatic publication of the setup executable for version tags.

### Changed

- frozen application updates now download and launch the official GitHub Release
  installer instead of attempting to use `pip`,
- the application and update feed version are now 1.6.0.

### Fixed

- Python 3.10 startup compatibility by avoiding the Python 3.11-only
  `datetime.UTC` constant.

## [1.5.0] - 2026-07-30

### Added

- optional GUI field for extra server IP addresses and private CIDRs,
- exhaustive TCP port discovery from `1` through `65535` for every explicitly
  entered exact IP address,
- exact public-host targeting while keeping public CIDR scanning blocked,
- `--target` CLI alias and exact-IP exhaustive discovery,
- additional common server ports in automatic host discovery,
- explicit-target tags, inventory metadata, and scan warnings.

### Changed

- extra GUI targets are combined with automatically detected LAN and VPN
  networks,
- exhaustive scans use adaptive worker limits and bounded progress events,
- update status now displays both the installed and feed versions.

### Fixed

- update checks now add a unique cache-busting query and no-cache headers,
- a stale update feed is shown as stale instead of incorrectly reporting
  `Up to date`,
- servers that block ICMP and listen only on a nonstandard TCP port can be
  discovered by entering their exact IP.

## [1.4.0] - 2026-07-30

### Added

- adaptive `Auto` parallelism based on the logical processors available to the
  running process,
- stage-specific discovery scheduling for ICMP, TCP, reverse DNS, and service
  fingerprinting,
- automatic analysis concurrency with support for `workers: auto`,
- `auto` support for GUI, YAML, `sdmap check --workers`, and
  `sdmap discover --workers`,
- GUI performance hint and completed-scan worker/CPU telemetry,
- generated inventory metadata describing the worker count and logical CPUs.

### Changed

- automatic discovery now scales from 32 up to 256 concurrent I/O workers,
- explicit worker limits now accept values from 1 to 256,
- parallel executors are bounded by the number of actual tasks,
- generated maps and new GUI templates use automatic parallelism by default.

### Fixed

- ICMP analysis no longer starts text-mode subprocess readers that can fail on
  incompatible Windows code pages.

## [1.3.0] - 2026-07-30

### Added

- automatic non-blocking update check when the desktop GUI starts,
- manual `Check for updates` action with a visible version state,
- confirmed one-click installation into the current virtual environment,
- safe source-checkout updates limited to a clean `main` branch and
  `git pull --ff-only`,
- restart action that reopens the selected service map after an update,
- version manifest and updater unit-test coverage.

### Fixed

- Windows discovery no longer depends on the active ANSI code page when
  decoding PowerShell, ARP, or other redirected command output,
- missing subprocess output is handled as an empty result instead of causing
  an `AttributeError` during discovery.

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
