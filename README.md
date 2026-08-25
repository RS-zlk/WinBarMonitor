# WinBarMonitor

[![CI](https://github.com/RS-zlk/WinBarMonitor/actions/workflows/ci.yml/badge.svg)](https://github.com/RS-zlk/WinBarMonitor/actions/workflows/ci.yml)

WinBarMonitor is a dependency-free [SwiftBar](https://swiftbar.app/) plugin
that puts live metrics from a remote Windows PC in the macOS menu bar. It
shows CPU, memory, C: drive, network, uptime, top processes, and optional
NVIDIA GPU data. The remote command performs read-only PowerShell/CIM queries
and an optional `nvidia-smi` query.

[中文文档](README.zh-CN.md)

## Features

- Generic SSH alias by default: `windows-monitor`.
- Displays the remote hostname, then the configured alias, then `Windows PC`
  when the hostname is unavailable.
- Green online, yellow cached, and red offline custom PNG icons.
- Last successful metrics are cached locally for short offline visibility.
- Successful samples are retained in a local SQLite database for 30 days by
  default, with a self-contained browser dashboard.
- Machine-specific settings live in a Git-ignored `.winbar.env` file.
- No third-party Python packages and no service hosted by this project.
- Handles no NVIDIA GPU, multiple GPUs, `N/A` values, and slow SSH links.

## Requirements

- macOS with Python 3 and [SwiftBar](https://swiftbar.app/).
- The macOS `ssh` client and an SSH alias that reaches the Windows host.
- Windows OpenSSH Server, PowerShell, and CIM/WMI access for the account.
- NVIDIA drivers and `nvidia-smi` only if GPU metrics are wanted.

The project has zero third-party Python dependencies. It uses only Python's
standard library, POSIX shell utilities, macOS `ssh`, and Windows built-ins.

## Installation

1. Install and launch SwiftBar, then choose its plugin folder.
2. Create a machine-local configuration file:

   ```sh
   cp .winbar.env.example .winbar.env
   ```

   Edit `.winbar.env` and set `WINBAR_SSH_ALIAS` to the SSH config alias for
   the Windows PC. This local file is ignored by Git.
3. Verify a non-interactive connection:

   ```sh
   ssh windows-monitor  # Replace with the alias configured in .winbar.env.
   ```

4. Install the plugin:

   ```sh
   ./install.sh
   ```

Refresh SwiftBar. The generated plugin filename controls SwiftBar's refresh
interval. The installer copies the local configuration into SwiftBar with
owner-only permissions. Opening the menu is immediate and does not trigger a
new SSH request; use the manual refresh action when needed.

## Windows OpenSSH and keys

On Windows, install and start the OpenSSH Server optional feature, permit SSH
through the Windows Firewall, and use a dedicated least-privilege account.
From macOS, create a key with `ssh-keygen`, append its public key to the
Windows account's authorized keys file, and add a host entry to
`~/.ssh/config`:

```sshconfig
Host windows-monitor
    HostName windows.example.invalid
    User monitor
    IdentityFile ~/.ssh/id_ed25519
    IdentitiesOnly yes
```

Use a private network or VPN, restrict firewall sources, and keep SSH
`BatchMode` authentication enabled. Never commit private keys, passwords,
tokens, or real host addresses.

## Local configuration

`.winbar.env` uses shell-style `KEY=VALUE` lines. Keep real host aliases and
machine-specific paths in that file, never in tracked source files. The
installer reads the file to determine the plugin location and refresh
interval, then copies it beside the installed support module. Runtime
environment variables override values from the file.

All settings are optional:

| Variable | Default | Meaning |
| --- | --- | --- |
| `WINBAR_SSH_ALIAS` | `windows-monitor` | SSH config alias |
| `WINBAR_REFRESH_SECONDS` | `30` | Positive plugin refresh interval |
| `WINBAR_SSH_TIMEOUT` | `15` | Positive total collection timeout |
| `WINBAR_CACHE_PATH` | `~/.cache/winbar-monitor/cache.json` | Local cache file |
| `WINBAR_HISTORY_PATH` | `~/.local/share/winbar-monitor/history.sqlite3` | Local history database |
| `WINBAR_REPORT_PATH` | `~/.local/share/winbar-monitor/history.html` | Generated dashboard |
| `WINBAR_RETENTION_DAYS` | `30` | Raw sample retention period |
| `SWIFTBAR_PLUGIN_DIR` | SwiftBar's standard plugin folder | Installation location |

Set `WINBAR_CONFIG_FILE=/path/to/settings.env` when running `install.sh` or
`uninstall.sh` to use a differently named local file. Values for numeric
settings are clamped to a minimum of one.

## Historical dashboard

Each successful collection appends one sample to SQLite and regenerates a
self-contained HTML report. The SwiftBar menu only contains a
`View history` action; all summaries and charts are shown in the browser.

The report provides 24-hour, 7-day, and 30-day views for CPU/GPU utilization,
memory/VRAM, GPU temperature/power, and network rates. Multi-GPU history uses
the highest utilization and temperature plus combined VRAM and power. Failed
collections are omitted instead of being stored as zero values. No external
JavaScript, analytics, fonts, or network requests are used by the report.
Hover over a chart point to see its local timestamp and series values.

## Security and privacy

The plugin sends one encoded, read-only PowerShell command over SSH. It does
not upload metrics, accept inbound connections, or store credentials. SSH
keys, cache, history, and report remain under the user's control. The cache can
contain a hostname and process names; history reveals machine-usage patterns.
Do not attach generated files to public issues. Review the command and the
[security policy](SECURITY.md) before deploying on a managed machine.

## Uninstall

Run the same configuration used for installation:

```sh
./uninstall.sh
```

This removes generated SwiftBar files and its copied configuration. It leaves
the source `.winbar.env`, cache, history, and report untouched. Delete local
monitoring data manually if desired:

```sh
rm -f "$HOME/.cache/winbar-monitor/cache.json"
rm -f "$HOME/.local/share/winbar-monitor/history.sqlite3" \
      "$HOME/.local/share/winbar-monitor/history.sqlite3-shm" \
      "$HOME/.local/share/winbar-monitor/history.sqlite3-wal" \
      "$HOME/.local/share/winbar-monitor/history.html"
```

## Troubleshooting

- `ssh windows-monitor` first; fix SSH authentication or name resolution
  before debugging the plugin.
- If the menu shows yellow, the last collection failed and the local cache is
  being displayed. Red means no usable cache exists.
- If GPU fields are `N/A`, check that `nvidia-smi` works in the Windows SSH
  session. Systems without NVIDIA hardware remain supported and show no GPU
  section.
- For slow links, increase `WINBAR_SSH_TIMEOUT` and use SSH connection
  multiplexing. Do not expose the Windows SSH service to the public internet.

## Development and tests

Run the standard-library test suite and shell syntax checks:

```sh
python3 -m unittest discover -s tests -v
python3 -m py_compile winbar_monitor.py winbar.1m.py
sh -n install.sh uninstall.sh
```

Tests use synthetic fixtures only; CI never makes a real SSH connection.
See [CONTRIBUTING.md](CONTRIBUTING.md) for the contribution workflow.

No live-host screenshot or generated report is committed. Tests use synthetic
fixtures so repository artifacts do not expose machine data.

## License

MIT. See [LICENSE](LICENSE).
