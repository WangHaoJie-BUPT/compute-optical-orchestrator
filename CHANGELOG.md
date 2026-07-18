# Changelog

All notable changes to this project will be documented in this file. The
project intends to use Semantic Versioning after the first public release.

## [Unreleased]

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

## Public release naming

The first reviewed public release should be tagged `v0.1.0`. Internal labels
such as v8-v13 describe development milestones and should not become a public
version history.
