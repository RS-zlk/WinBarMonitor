# Contributing

Thanks for helping improve WinBarMonitor.

## Before submitting a change

- Keep the project free of third-party Python packages.
- Do not add real hostnames, IP addresses, usernames, private keys, tokens,
  cache files, or personal paths.
- Keep remote operations read-only and preserve the offline/cache behavior.
- Use synthetic fixtures for tests; never require a live SSH connection.

## Local checks

```sh
python3 -m unittest discover -s tests -v
sh -n install.sh uninstall.sh winbar.1m.py
```

Document user-visible changes in `CHANGELOG.md`. Keep commits focused and write
commit messages in English using `<type>: <imperative description>`, for example
`fix: handle an unavailable GPU`. Pull requests should explain the behavior
change, test coverage, and any security implications.
