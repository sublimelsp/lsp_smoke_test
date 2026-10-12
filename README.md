# LSP smoke test

A GitHub Actions workflow that checks that an LSP package for Sublime Text works: it installs the package in Sublime
Text together with [LSP](https://github.com/sublimelsp/LSP), opens a sample file, waits for the language server to
start and checks that it reports the expected diagnostics.

## Usage

Add this workflow to the package repository. It's the same in every package:

```yaml
# .github/workflows/smoke-test.yml
name: Smoke test

on:
  push:
    branches:
      - main
  pull_request:
  workflow_dispatch:

jobs:
  smoke:
    uses: sublimelsp/lsp_smoke_test/.github/workflows/smoke-test.yml@v1
```

Then add a `tests/smoke/` directory with a `smoke.json` file and the files the server needs:

```
tests/smoke/
  smoke.json
  sample.py
  ruff.toml
```

```json
{
    "file": "sample.py",
    "expect": {"source": "Ruff", "code": "F401"}
}
```

The directory is copied to a temporary location and opened as the only folder of the Sublime Text window, so it acts as
the workspace root. Put any project files the server needs in it, such as `Cargo.toml` or `package.json`.

If the server is a linter, also add its configuration with only the rules you check for, so that the result doesn't
change when the server's default rules change.

See [examples](examples) for more.

## smoke.json

| Key | Default | Description |
|---|---|---|
| `file` | | The file to open, relative to the fixture directory. |
| `server` | The package name | Name of the LSP session. Only needed for plugins whose name differs from the package name. |
| `expect` | `{"min_diagnostics": 1}` | What to check, see below. |
| `startup_timeout` | `120` | Seconds to wait for the server to start, including downloading or installing it. |
| `diagnostics_timeout` | `30` | Seconds to wait for the expected diagnostics after the server has started. |

`expect` is either `"start"`, which only checks that the server starts, or an object. With an object, the test waits for
at least `min_diagnostics` diagnostics (default `1`) matching all of the given keys:

| Key | Matches |
|---|---|
| `source` | The diagnostic's `source`, exactly. |
| `code` | The diagnostic's `code`, exactly. |
| `severity` | The diagnostic's `severity` number (1 = error, 2 = warning, 3 = information, 4 = hint). |
| `message` | The diagnostic's `message`, as a regular expression. |

When the test fails, it prints the sessions running in the window, the diagnostics received and the last lines of the
server log.

## Workflow inputs

All inputs are optional.

| Input | Default | Description |
|---|---|---|
| `lsp-ref` | Latest release | Branch, tag or commit of LSP to test against. |
| `os` | `'["ubuntu-latest", "macos-latest"]'` | JSON list of runners. |
| `fixture-dir` | `tests/smoke` | Directory containing `smoke.json`. |
| `package-name` | Repository name | Name of the package under test. |

## Servers that need a toolchain

A reusable workflow can't run extra steps before the test. If the server needs something installed first, such as Go
for gopls, use the action directly:

```yaml
jobs:
  smoke:
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, macos-latest]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v7
      - uses: actions/setup-go@v6
      - uses: sublimelsp/lsp_smoke_test@v1
```

The action takes the same inputs as the workflow, except `os`, plus `package-path` (default `.`) for where the package
is checked out.

## Releases

Callers use the `v1` tag. After a change, move it to the new commit:

```sh
git tag -f v1 && git push -f origin v1
```

The reusable workflow checks out the action from its own commit, so the workflow and the action always match.
