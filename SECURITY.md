# Security Policy

## Supported versions

This project is distributed as source you run yourself. Security fixes are made
against the latest code on the `main` branch (and the live web version on GitHub
Pages). Please make sure you are running the current version before reporting.

| Version            | Supported |
| ------------------ | --------- |
| `main` / latest    | ✅        |
| older snapshots    | ❌        |

## Reporting a vulnerability

Please **do not** open a public issue for security problems.

Instead, report privately through GitHub's
[**private vulnerability reporting**](https://github.com/andrefecto/Snapchat-Memories-Downloader/security/advisories/new)
(the **Security → Report a vulnerability** button on the repository). If that is
unavailable, contact the maintainer [@andrefecto](https://github.com/andrefecto)
through GitHub.

When reporting, please include:

- A description of the issue and its potential impact.
- Steps to reproduce (a minimal proof of concept if possible).
- The affected version (Python CLI or web) and your OS / browser.

**Never include real Snapchat memories, download URLs, or GPS coordinates in a
report.** Redact or synthesize sample data.

You can expect an acknowledgement within a reasonable time, and we'll keep you
updated on the fix. Coordinated disclosure is appreciated — please give us a chance
to release a fix before publishing details.

## Scope & threat model

This is a **privacy-first, client-side tool**:

- The **web** version runs entirely in your browser — no upload, no server, no
  analytics.
- The **Python** version runs entirely on your machine.
- The only network activity is downloading a user's *own* memories from Snapchat's
  own URLs (older export format). The new bundled-export path makes **no** network
  requests at all.

Things especially worth reporting:

- Any code path that would transmit user data off-device.
- Handling of untrusted input from `memories_history.html` / `memories.html` /
  `memories_history.json` or downloaded media (e.g. path traversal when writing
  files, ZIP handling, or magic-byte parsing).
- Dependency vulnerabilities (Dependabot is enabled, but reports are welcome).
