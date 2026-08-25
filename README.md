# WinBarMonitor

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
2. Create an SSH config alias named `windows-monitor` (or set
   `WINBAR_SSH_ALIAS`).
3. Verify a non-interactive connection:

   ```sh
   ssh windows-monitor
   ```

4. Install the plugin:

   ```sh
   ./install.sh
   # Optional: choose the refresh interval and plugin directory.
   WINBAR_REFRESH_SECONDS=10 SWIFTBAR_PLUGIN_DIR="/path/to/plugins" ./install.sh
   ```

Refresh SwiftBar. The generated plugin filename controls SwiftBar's refresh
interval; opening the menu also supports a manual refresh action.

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

## Configuration

All settings are optional environment variables:

| Variable | Default | Meaning |
| --- | --- | --- |
| `WINBAR_SSH_ALIAS` | `windows-monitor` | SSH config alias |
| `WINBAR_REFRESH_SECONDS` | `30` | Positive plugin refresh interval |
| `WINBAR_SSH_TIMEOUT` | `15` | Positive total collection timeout |
| `WINBAR_CACHE_PATH` | `~/.cache/winbar-monitor/cache.json` | Local cache file |

Values for refresh and timeout are clamped to a minimum of one second. The
cache is written atomically after a successful collection and is never sent
to a server.

## Security and privacy

The plugin sends one encoded, read-only PowerShell command over SSH. It does
not upload metrics, accept inbound connections, or store credentials. SSH
keys and the cache remain under the user's control. Review the command and
the [security policy](SECURITY.md) before deploying on a managed machine.

## Uninstall

Run the same configuration used for installation:

```sh
./uninstall.sh
```

This removes generated SwiftBar files and leaves the local cache untouched.
Delete the cache manually if desired:

```sh
rm -f "$HOME/.cache/winbar-monitor/cache.json"
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
sh -n install.sh uninstall.sh winbar.1m.py
```

Tests use synthetic fixtures only; CI never makes a real SSH connection.
See [CONTRIBUTING.md](CONTRIBUTING.md) for the contribution workflow.

## License

MIT. See [LICENSE](LICENSE).
