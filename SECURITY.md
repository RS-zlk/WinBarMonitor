# Security Policy

## Scope

In the recommended `windows-files` mode, Windows runs a local collector that
writes `latest.json` and JSONL history under `C:\ProgramData\WinBarMonitor`.
The Mac sends read-only PowerShell commands through the user's existing SSH
configuration to read those files. The collector does not provide a server,
accept network connections, or transmit metrics to a project-controlled
service. Legacy `direct-ssh` mode runs the collection query through SSH.

## Safe deployment

- Use SSH public-key authentication and a least-privilege Windows account.
- Restrict the Windows Firewall to trusted network sources or a VPN.
- Run `windows/install-windows-collector.ps1` only from a reviewed checkout.
  The installer registers a boot-time `SYSTEM` task so collection continues
  before an interactive sign-in. Review script changes before upgrading.
- Supply `-ReaderAccount` for the exact OpenSSH account. It receives only
  `ReadAndExecute` access to the record directory; do not grant record access
  to `Everyone` or all local users.
- Keep private keys, passwords, tokens, real addresses, `.winbar.env`, cache,
  Windows JSONL history, history databases, and generated reports out of the
  repository and issue tracker. Current snapshots can include hostnames and
  process names, while history reveals machine-usage patterns.
- Treat `.winbar.env` as trusted local shell input: the installer sources it.
  Never use a configuration file downloaded from an untrusted source.
- Do not copy a live SQLite database while it is being written. The project
  transfers line-oriented Windows history records instead, then stores its
  independent Mac-side SQLite mirror.
- Review changes to `POWERSHELL_SCRIPT` and `windows/*.ps1` before upgrading.

## Reporting a vulnerability

Do not publish exploitable details in a public issue. Contact the repository
maintainers privately with a description, affected version, reproduction
steps, and suggested mitigation. Please allow reasonable time for a fix before
public disclosure.
