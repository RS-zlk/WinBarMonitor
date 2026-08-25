# Changelog

All notable changes to this project are documented here.

## [Unreleased]

### Added

- Git-ignored `.winbar.env` workflow with a safe public example.
- Local SQLite history with configurable retention.
- Self-contained 24-hour, 7-day, and 30-day browser dashboard.
- SwiftBar action for opening the dashboard without showing statistics in the
  menu itself.

### Changed

- Opening the SwiftBar menu no longer forces a synchronous SSH refresh.
- Installation copies local configuration with owner-only permissions.

## [0.1.0] - 2026-08-25

### Added

- First public release for SwiftBar and remote Windows monitoring.
- Generic `windows-monitor` SSH alias and hostname-aware menu label.
- Online, cached, and offline colored PNG status icons.
- Standard-library tests, shell checks, and GitHub Actions CI.
- English and Simplified Chinese documentation.
