# Contributing

Thanks for helping improve WinBarMonitor.

## Before submitting a change

- Keep the project free of third-party Python packages.
- Do not add real hostnames, IP addresses, usernames, private keys, tokens,
  `.winbar.env`, cache/history databases, generated reports, or personal paths.
- Add new configurable settings to `.winbar.env.example` with generic values
  and document them in both READMEs.
- Keep remote operations read-only and preserve the offline/cache behavior.
- Use synthetic fixtures for tests; never require a live SSH connection.

## Local checks

```sh
python3 -m unittest discover -s tests -v
python3 -m py_compile winbar_monitor.py winbar.1m.py
sh -n install.sh uninstall.sh
```

Before committing, inspect `git status --ignored` and `git diff --cached`.
Machine-local `.winbar.env`, SQLite files, cache JSON, and generated HTML must
remain untracked. Use only synthetic hostnames and metrics in tests.

Document user-visible changes in `CHANGELOG.md`. Keep commits focused and write
commit messages in English using `<type>: <imperative description>`, for example
`fix: handle an unavailable GPU`. Pull requests should explain the behavior
change, test coverage, and any security implications.
