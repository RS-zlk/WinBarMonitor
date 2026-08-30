# Changelog

All notable changes to this project are documented here.

## [Unreleased]

### Added

- Windows-local collector with boot-time deployment, atomic current snapshots,
  UTC-day JSONL history, retention, and a read-only OpenSSH sharing option.
- `windows-files` data source for the Mac plugin, including automatic history
  backfill after the Mac reconnects.
- Windows GitHub Actions validation that parses PowerShell scripts and runs one
  local collection.
- Git-ignored `.winbar.env` workflow with a safe public example.
- Local SQLite history with configurable retention.
- Self-contained 24-hour, 7-day, and 30-day browser dashboard.
- SwiftBar action for opening the dashboard without showing statistics in the
  menu itself.
- Hover tooltips that show the timestamp and values for chart data points.
- Configurable sustained-low GPU/VRAM alert with native macOS notification,
  persisted detector state, and automatic re-arming after GPU activity.

### Changed

- Opening the SwiftBar menu no longer forces a synchronous SSH refresh.
- Installation copies local configuration with owner-only permissions.

### Fixed

- Made Windows `latest.json` replacement compatible with Windows PowerShell
  5.1 after the initial snapshot already exists.
- Kept Windows PowerShell sources ASCII-only to avoid encoding-dependent
  parser failures on Windows PowerShell 5.1.

## [0.1.0] - 2026-08-25

### Added

- First public release for SwiftBar and remote Windows monitoring.
- Generic `windows-monitor` SSH alias and hostname-aware menu label.
- Online, cached, and offline colored PNG status icons.
- Standard-library tests, shell checks, and GitHub Actions CI.
- English and Simplified Chinese documentation.
