# Security CI

`ybcal`'s recommendations justify constants that are baked into node releases, so the integrity of its
inputs, its code and its dependencies is part of the release. Four workflows check it, beside `ci.yml`
(tests, lint, kernel parity). The pattern is the same in every Yellowback repository.

| Leg | Workflow / job | Runs on | Gate | Results |
|---|---|---|---|---|
| Semgrep | `semgrep.yml` | PR (diff-aware), push, nightly 02:17 UTC | a PR fails on a new **critical** finding | run summary, code scanning (`semgrep`), at most four rolling PRs `semgrep/<bucket>` |
| CodeQL | `codeql.yml` (python, actions; `security-extended`, no build) | PR, push to `upgrade/vault`, weekly | none on pre-existing alerts; new ones are annotated on the PR | code scanning (`codeql-python`, `codeql-actions`) |
| zizmor | `secops.yml` / `zizmor` | PR, push to `upgrade/vault`, nightly 03:43 UTC | any finding in a workflow or `dependabot.yml` | run summary, code scanning (`zizmor`) |
| gitleaks | `secops.yml` / `gitleaks` | PR: the PR's commits; push / nightly / dispatch: full history | any leak not in `.gitleaksignore` | run summary (redacted), code scanning (`gitleaks`) |
| OSV-Scanner | `secops.yml` / `osv-scanner` | PR, push, nightly | a known vulnerability in any lockfile not excepted in `osv-scanner.toml` | run summary, code scanning (`osv-scanner`) |
| pip-audit | `secops.yml` / `pip-audit` | PR, push, nightly | any known vulnerability in `requirements-lock.txt` or the CI tool locks | run summary |
| Code-scanning triage | `code-scanning.yml` | push to `upgrade/vault` changing the dismissals file, nightly 03:53 UTC, dispatch (`dry_run`) | none; dismisses alerts listed in `contrib/secops/code-scanning-dismissals.json` | job log, artifact `code-scanning-alerts` |
| Dependabot | `.github/dependabot.yml` | GitHub's schedule | security updates only, one grouped PR per ecosystem (pip, github-actions); version updates off | PRs |

## What is scanned

- **Dependencies.** `requirements-lock.txt` is ybcal's dependency set (`pyproject.toml` with the `dev`
  extra), exact-pinned and hash-locked for CPython 3.11; its header has the regeneration command. Use it for
  a calibration run whose result goes into a release:
  `pip install --require-hashes -r requirements-lock.txt && pip install --no-deps -e .`
  The CI tools are hash-locked too: `contrib/semgrep/requirements.txt` (shared with the node lines),
  `contrib/secops/requirements-{zizmor,pip-audit}.txt`. gitleaks and osv-scanner are release binaries
  checked against the sha256 recorded in `secops.yml`. Every action is pinned by commit SHA.
- **Code.** Semgrep with the pinned packs (semgrep-rules `python/lang/security`, `bash`,
  `generic/secrets`, `yaml/github-actions`; Trail of Bits `python`, `generic`, `yaml/github-actions`) and
  this repository's rules, `contrib/semgrep/rules/ybcal.yaml`:
  - `ybcal.data.network-fetch-without-provenance`: network I/O only in `src/ybcal/data/fetch.py`, which
    writes the provenance sidecar (URLs, `fetched_at`, sha256) for every input it fetches (and the local
    devnet RPC client);
  - `ybcal.data.unsafe-deserialization`: no pickle / marshal / shelve / `yaml.load` without `SafeLoader` /
    `np.load(allow_pickle=True)` on input files;
  - `ybcal.subprocess.shell`: commands run with an argv list, never through a shell.

  All three are `critical`, so a PR that introduces one fails. Their test cases are
  `contrib/semgrep/rules/ybcal.py` (`semgrep --test contrib/semgrep/rules`). The vendored reference model
  (`src/ybcal/model/reference*.py`, byte-identical to ycash6 and checked by `ybcal verify`) is not scanned
  here (`.semgrepignore`); ycash6's own scan covers it.

## Accepting a finding

Every exception carries a reason a reviewer can check:

- **Semgrep**: `contrib/semgrep/accepted.json` (by fingerprint, or a rule over paths), or `config.json`
  `exclude_rules` / `audit_rules` / `bucket_overrides`. See `contrib/semgrep/README.md`.
- **OSV-Scanner**: an `[[IgnoredVulns]]` entry in `osv-scanner.toml` with `reason` and an `ignoreUntil` at
  most 90 days out. Prefer fixing: bump the package in the lock (header command, `--upgrade-package`).
- **pip-audit**: fix it in the lock; an unfixable advisory gets `--ignore-vuln ID` in `secops.yml` with
  the reason in a comment.
- **gitleaks**: a fingerprint in `.gitleaksignore` with a reason comment, for test vectors and public
  constants only. A real secret is rotated first, then removed.
- **Code-scanning alerts** (any tool): a false positive, test-only hit or accepted risk is an entry in
  `contrib/secops/code-scanning-dismissals.json` (tool, rule, path glob, reason, comment);
  `.github/workflows/code-scanning.yml` dismisses the matching alerts. A real finding is fixed, never listed.
- **zizmor**: fix the workflow; an intended pattern takes an inline `# zizmor: ignore[<audit>]` with the
  reason beside it.

## Repository settings

- Code scanning must be available (free for public repositories) for the SARIF uploads.
- For the Semgrep rolling PRs: *Settings → Actions → General → Allow GitHub Actions to create and approve
  pull requests*; optionally a `SEMGREP_PR_TOKEN` secret so CI runs on them (`contrib/semgrep/README.md`).
- Dependabot security updates on (*Settings → Code security*).
