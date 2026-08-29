# Security Policy

## Scope

WinBarMonitor runs a read-only PowerShell/CIM collection command through the
user's existing SSH configuration. It does not provide a server, accept
network connections, or transmit metrics to a project-controlled service.

## Safe deployment

- Use SSH public-key authentication and a least-privilege Windows account.
- Restrict the Windows Firewall to trusted network sources or a VPN.
- Keep private keys, passwords, tokens, real addresses, `.winbar.env`, cache,
  history databases, and generated reports out of the repository and issue
  tracker. Cache files can include hostnames and process names, while history
  reveals machine-usage patterns.
- Treat `.winbar.env` as trusted local shell input: the installer sources it.
  Never use a configuration file downloaded from an untrusted source.
- Review changes to `POWERSHELL_SCRIPT` before upgrading.

## Reporting a vulnerability

Do not publish exploitable details in a public issue. Contact the repository
maintainers privately with a description, affected version, reproduction
steps, and suggested mitigation. Please allow reasonable time for a fix before
public disclosure.
