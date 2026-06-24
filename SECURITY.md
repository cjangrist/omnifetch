# Security Policy

## Reporting a vulnerability

Please report security issues **privately** via
[GitHub Security Advisories](https://github.com/cjangrist/omnifetch/security/advisories/new)
(Repository → **Security** → **Report a vulnerability**). Do not open public issues for
suspected vulnerabilities. We aim to acknowledge reports within 3 business days.

## Supply-chain hardening

The controls below are enforced in CI on every push and pull request.

### Dependencies

- **Exact pins + hash verification.** Every direct dependency is pinned to an exact version
  in `pyproject.toml`, and `uv.lock` records cryptographic hashes for every resolved package
  (including transitives). CI installs with `UV_LOCKED=1` (`uv sync --locked`), which fails
  if the lockfile drifts or a hash mismatches — preventing silent dependency substitution
  and dependency-confusion attacks.
- **Automated, reviewed updates.** Dependabot opens grouped weekly PRs for Python
  dependencies and GitHub Actions (`.github/dependabot.yml`); every update must pass the full
  CI gate before it can merge.
- **Vulnerability scanning.** `pip-audit` runs in CI (the `security` job) against the locked
  dependency set.
- **Dependency review.** `actions/dependency-review-action` blocks pull requests that
  introduce dependencies with known high-severity vulnerabilities.

### CI / build integrity

- **SHA-pinned Actions.** Every GitHub Action is pinned to a full commit SHA (not a mutable
  tag), so a hijacked tag cannot inject code into the pipeline. Dependabot keeps the SHAs
  current.
- **Least privilege.** The workflow `GITHUB_TOKEN` defaults to no permissions
  (`permissions: {}`); each job is granted only the scopes it needs.
- **Runner hardening.** `step-security/harden-runner` runs in deny-by-default (`block`) mode
  on the lint, test, audit, build, CodeQL, and dependency-review jobs: only an explicit
  allowlist of endpoints (GitHub, PyPI, Sigstore) is reachable and all other egress is
  blocked, neutralizing data exfiltration from a compromised dependency or action. `sudo` is
  disabled on the runner. (The OpenSSF Scorecard job runs in audit mode by design — it
  intentionally probes the wider ecosystem to compute its score.)
- **Signed build provenance.** Distribution artifacts are built in CI and attested with
  [SLSA build provenance](https://slsa.dev) (`actions/attest-build-provenance`), so anyone
  can cryptographically verify a wheel was built from this source by this workflow:
  `gh attestation verify <wheel> --repo cjangrist/omnifetch`.

### Code analysis

- **SAST.** CodeQL analyzes the code for security issues on every push and PR.
- **OpenSSF Scorecard.** The project's supply-chain posture is continuously scored and
  published; the score badge is in the README.

## Supported versions

This project is pre-1.0; only the latest commit on `main` is supported.
