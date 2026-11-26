# Clean-room demo runs, 2026-11-26

The one-command demo (`uv run --locked demo`, run twice in a row) was run from a fresh checkout on three GitHub-hosted runners, and once by the author on their own Mac.

- Commit: `97c7134e62aeb2a22ae1a6d391b9bbd27aec3d60`
- Workflow: GitHub Actions run 45909350949 (the `cleanroom` workflow, started by hand on main)

## Runners

Each job installs the locked environment (`uv sync --locked`, Python 3.12, no cache) and runs the demo twice in PowerShell. The second run reuses the DuckDB file and the downloaded files. Both demo runs exited 0 on every system.

| OS | Runner image | Conclusion | Duration | Both demo runs exit 0 |
| --- | --- | --- | --- | --- |
| ubuntu-latest | ubuntu-26.04 | success | 3m 53s | yes |
| macos-latest | macos-26-arm64 | success | 2m 51s | yes |
| windows-latest | windows-2025-vs2026 | success | 5m 25s | yes |

## The author's run on a Mac

A manual run, as the author reported it: a fresh clone of main, then `uv run demo`.

| Run | macOS | Wall time | Exit status | Commit |
| --- | --- | --- | --- | --- |
| Mac (manual) | 26.5.1 | 154.89 s | 0 | 97c7134 |

The author's run built the gold tables (`fct_trips` 6,439,594 rows) and all three reconciliations reported OK. 256 of 6,439,850 yellow trip rows (0.004%) went to quarantine.

## What this proves

On 2026-11-26 the demo ran from a fresh checkout to a green result on Linux, macOS and Windows runners, and on the author's Mac, at this commit. It is a dated claim: a later runner-image change, or a network that blocks the data host, can break a run that passed, which is why the `cleanroom` workflow can be run again at any time.
