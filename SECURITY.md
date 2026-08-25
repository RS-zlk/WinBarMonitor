# Security Policy

## Scope

WinBarMonitor runs a read-only PowerShell/CIM collection command through the
user's existing SSH configuration. It does not provide a server, accept
network connections, or transmit metrics to a project-controlled service.

## Safe deployment

- Use SSH public-key authentication and a least-privilege Windows account.
- Restrict the Windows Firewall to trusted network sources or a VPN.
- Keep private keys, passwords, tokens, real addresses, and cache files out of
  the repository and issue tracker.
- Review changes to `POWERSHELL_SCRIPT` before upgrading.

## Reporting a vulnerability

Do not publish exploitable details in a public issue. Contact the repository
maintainers privately with a description, affected version, reproduction
steps, and suggested mitigation. Please allow reasonable time for a fix before
public disclosure.
