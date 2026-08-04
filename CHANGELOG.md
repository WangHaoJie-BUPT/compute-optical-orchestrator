# Changelog

All notable changes to this project will be documented in this file. The
project uses Semantic Versioning for public releases.

## [Unreleased]

## [0.1.0] - 2026-08-04

### Added

- Durable v3 admission queue with tenant rotation, priority aging,
  backpressure, retry, cancellation, and stale-claim recovery.
- Official Kubernetes Python Client runtime adapter with local and in-cluster
  credential discovery.
- Accelerator-aware scheduling for configurable GPU/NPU extended resources
  and node capability constraints.
- SQLite compare-and-swap runtime versions and persistent link/slot claims.
- Prometheus queue, controller, Kubernetes, and optical-resource metrics,
  Grafana dashboards, and alert rules.
- Reproducible architecture, demo, performance-measurement, and
  fault-injection documentation.

### Changed

- Runtime deployment image milestone advanced from v7 to internal v13.
- Test discovery now includes every `scripts/test_*.py` file automatically.

### Security

- Profile-controlled v2/v3 APIs prevent clients from supplying arbitrary
  images or shell commands.
- API-key authentication covers operational task and queue endpoints.

## Development naming

Internal labels such as v8-v13 describe development milestones and are not
part of the public version history.

[Unreleased]: https://github.com/bupt20220407/compute-optical-orchestrator/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/bupt20220407/compute-optical-orchestrator/releases/tag/v0.1.0
