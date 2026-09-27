# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.9.x   | :white_check_mark: |
| 0.x     | :white_check_mark: |

## Reporting a Vulnerability

If you find a security issue, **do not open a public issue**. Use GitHub's
private vulnerability reporting instead:

1. Open the repository's **Security** tab.
2. Click **Report a vulnerability**.
3. Describe the issue, the affected file(s), and steps to reproduce.

Expect an initial response within 7 days. If the report is confirmed, a fix
will be released and credited in the release notes (unless you prefer to stay
anonymous).

## Scope Notes

This tool runs with elevated privileges by design (power-scheme switching
requires it) and:

- makes **no network connections** of its own,
- stores **no credentials** — there is nothing to leak by design,
- only shells out to `powercfg` with GUIDs resolved from `powercfg /list`,
- only loads AMD's own driver libraries (ADLX) already present on the system.

A report is in scope if it shows any of the above no longer holds (unexpected
network activity, credential handling, command injection, privilege use beyond
what the README describes). General hardening ideas are welcome as regular
issues.
