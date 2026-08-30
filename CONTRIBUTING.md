# Contributing

Thanks for helping improve WinBarMonitor.

## Before submitting a change

- Keep the project free of third-party Python packages.
- Do not add real hostnames, IP addresses, usernames, private keys, tokens,
  `.winbar.env`, cache/history databases, generated reports, or personal paths.
- Add new configurable settings to `.winbar.env.example` with generic values
  and document them in both READMEs.
- Keep Mac remote operations read-only and preserve the offline/cache behavior.
- Keep Windows collector history free of process names/PIDs and compatible with
  the documented JSONL record schema.
- Use synthetic fixtures for tests; never require a live SSH connection.

## Local checks

```sh
python3 -m unittest discover -s tests -v
python3 -m py_compile winbar_monitor.py winbar.1m.py
sh -n install.sh uninstall.sh
```

On Windows, parse and exercise the one-shot collector before changing its
deployment scripts:

```powershell
& .\windows\collect-winbar.ps1 -DataDirectory "$env:TEMP\winbar-monitor-test" -RetentionDays 1
Get-Content -Raw "$env:TEMP\winbar-monitor-test\latest.json"
```

Before committing, inspect `git status --ignored` and `git diff --cached`.
Machine-local `.winbar.env`, SQLite files, cache JSON, Windows record files,
and generated HTML must remain untracked. Use only synthetic hostnames and
metrics in tests.

Document user-visible changes in `CHANGELOG.md`. Keep commits focused and write
commit messages in English using `<type>: <imperative description>`, for example
`fix: handle an unavailable GPU`. Pull requests should explain the behavior
change, test coverage, and any security implications.
