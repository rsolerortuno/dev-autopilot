# Security policy

## Supported version

Security fixes are applied to the latest release line.

## Reporting a vulnerability

Do not publish credentials, exploit details, or private data in a public issue.
Use GitHub's private vulnerability reporting for this repository. Include the
affected version, a minimal reproduction, the expected security boundary, and
the observed result.

## Security boundaries

Dev Autopilot is fail-closed around repository scope, Git metadata, worker leases,
fencing tokens, bundle integrity, and path traversal. Real agent commands still
execute with the permissions of their configured runtime. Use an isolated
worktree and a container or disposable Colab runtime. Never store API keys,
OAuth sessions, browser cookies, SSH keys, or real `.env` files in Google Drive.

The local-process runtime is not a security sandbox. Network isolation and
provider-specific secret injection remain deployment responsibilities until the
planned sandbox milestone.
