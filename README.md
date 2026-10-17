# LSP smoke test

A GitHub Actions workflow that checks that an LSP package for Sublime Text works: it installs the package in Sublime
Text together with [LSP](https://github.com/sublimelsp/LSP), opens a sample file, waits for the language server to
start and checks that it reports the expected diagnostics or returns the expected hover.

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

Then add a `tests/smoke/` directory with a `smoke.yaml` file and the files the server needs:

```
tests/smoke/
  smoke.yaml
  sample.py
  ruff.toml
```

```yaml
# yaml-language-server: $schema=https://raw.githubusercontent.com/sublimelsp/lsp_smoke_test/v1/smoke.schema.json
file: sample.py
expected_diagnostics:
  source: Ruff
  code: F401
```

The first line points editors that use [yaml-language-server](https://github.com/redhat-developer/yaml-language-server),
such as Sublime Text with LSP-yaml or VS Code with the YAML extension, at [smoke.schema.json](smoke.schema.json), which
gives completion, documentation on hover and validation.

The directory is copied to a temporary location and opened as the only folder of the Sublime Text window, so it acts as
the workspace root. Put any project files the server needs in it, such as `Cargo.toml` or `package.json`.

If the server is a linter, also add its configuration with only the rules you check for, so that the result doesn't
change when the server's default rules change.

See [examples](examples) for more.

## smoke.yaml

`smoke.yml` works too. The file is checked against the schema's keys before Sublime Text is installed, so a mistake
such as an unknown key or a missing file fails the job right away.

| Key | Default | Description |
|---|---|---|
| `file` | | The file to open, relative to the fixture directory. |
| `session_name` | The package name | Name of the LSP session. Only needed for plugins whose name differs from the package name. |
| `expected_diagnostics` | | The diagnostics to wait for, see below. |
| `expected_hover` | | The hover to wait for, see below. |
| `syntax` | | Scope of the syntax to assign to the file, for example `source.yaml.esphome`, when it isn't assigned automatically by the file extension. Install the package that provides it with `extra-packages`. |
| `settings` | | Settings to apply while the test runs, by settings file name, for example `lsp_utils.sublime-settings: {nodejs_runtime: [system]}`. They are restored afterwards. |
| `diagnostics_timeout` | `180` | Seconds to wait for the expected diagnostics, or for the server to start before requesting the hover, including starting the server, which can include downloading or installing it. At most `240`: UnitTesting stops the run after 300 seconds without test output, which would hide the failure report. |
| `hover_timeout` | `10` | Seconds to wait for the expected hover after the server has started. At most `50`. |

`expected_diagnostics` describes the diagnostics to wait for: at least `min_count` (default `1`) diagnostics matching
all of the given keys. `expected_diagnostics: {}` waits for any diagnostic.

| Key | Matches |
|---|---|
| `source` | The diagnostic's `source`, exactly. |
| `code` | The diagnostic's `code`, exactly. |
| `severity` | The diagnostic's `severity` number (1 = error, 2 = warning, 3 = information, 4 = hint). |
| `message` | The diagnostic's `message`, as a regular expression. |

`expected_hover` describes a hover to request, for servers that don't report diagnostics for a small sample file. The
hover is requested again every second until the response matches or `hover_timeout` runs out, since servers can return
no hover while they are still processing the file.

| Key | Description |
|---|---|
| `at` | Text in the file to hover over, required. The hover is requested at the first character of its first occurrence. |
| `match` | A regular expression that the hover's contents must match. The contents are converted to a string first: the `value` of a `MarkupContent` or `MarkedString`, or the values of a list of `MarkedString`s joined with newlines. Markdown isn't rendered, so match its source, for example `\*\*SUMMARY\*\*`. By default, any non-empty hover matches. |

```yaml
file: sample.py
expected_hover:
  at: foo
  match: '\(variable\) foo: str'
```

Set `expected_diagnostics`, `expected_hover` or both. Only what is set is checked. Diagnostics and hover are checked
in separate tests, so with both, the server is started twice.

When a test fails, it prints the sessions running in the window, the diagnostics received and the last lines of the
server log.

## Workflow inputs

All inputs are optional.

| Input | Default | Description |
|---|---|---|
| `lsp-ref` | Latest release | Branch, tag or commit of LSP to test against. See [Testing against an LSP branch](#testing-against-an-lsp-branch). |
| `os` | `'["ubuntu-latest", "macos-latest", "windows-latest"]'` | JSON list of runners. |
| `fixture-dir` | `tests/smoke` | Directory containing `smoke.yaml`, or several, one per line. Each directory is a separate test, all run in the same Sublime Text session, so they need different names. |
| `package-name` | Repository name | Name of the package under test. |
| `extra-packages` | | Other packages to install, one per line, as `user/repo@ref`. Without `@ref`, the latest `x.y.z` tag is used. |

## Testing against an LSP branch

To run every package's smoke test against an LSP branch without changing each workflow's `lsp-ref`, set the
`LSP_SMOKE_TEST_LSP_REF` configuration variable, at the organization level or in a single repository, to a branch, tag
or commit of LSP. Every run that doesn't set `lsp-ref` then uses it, including runs for pushes and pull requests, so
delete the variable again when you're done. Runs that use it show a notice with the ref.

The reusable workflow picks the variable up on its own. Composite actions can't read configuration variables, so a
workflow that uses the action directly has to pass it in the environment:

```yaml
    env:
      LSP_SMOKE_TEST_LSP_REF: ${{ vars.LSP_SMOKE_TEST_LSP_REF }}
```

## Servers that need a toolchain

A reusable workflow can't run extra steps before the test. If the server needs something installed first, such as Go
for gopls, use the action directly:

```yaml
jobs:
  smoke:
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, macos-latest, windows-latest]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v7
      - uses: actions/setup-go@v6
      - uses: sublimelsp/lsp_smoke_test@v1
```

The action takes the same inputs as the workflow, except `os`, plus:

| Input | Default | Description |
|---|---|---|
| `install-package` | `true` | Whether to install the package checked out at `package-path`. `false` when there is no package under test. |
| `package-path` | `.` | Where the package under test is checked out. |
| `local-libraries` | | Libraries to replace with a local copy, one per line, as `name=path` to the library's directory. |

## Testing a library

A library such as lsp_utils can be tested through the packages that use it. Install them with `extra-packages`,
set `install-package` to `false`, replace the published library with the checkout using `local-libraries`, and give
every `smoke.yaml` a `session_name`:

```yaml
steps:
  - uses: actions/checkout@v7
    with:
      path: lsp_utils
  - uses: sublimelsp/lsp_smoke_test@v1
    with:
      install-package: false
      extra-packages: |
        sublimelsp/LSP-pyright
        sublimelsp/LSP-ruff
      local-libraries: lsp_utils=lsp_utils/lsp_utils
      fixture-dir: |
        lsp_utils/tests/smoke/pyright
        lsp_utils/tests/smoke/ruff
```

The library is replaced after Package Control has installed it, so at least one of the packages must depend on it.

## Development

LSP and UnitTesting are not on PyPI, so clone them for type checking:

```sh
git clone --depth 1 https://github.com/sublimelsp/LSP.git .deps/LSP
git clone --depth 1 https://github.com/SublimeText/UnitTesting.git .deps/UnitTesting
uv sync
uv run pyright
uv run ruff check
uv run ruff format --check
```

The harness runs in Sublime Text's Python 3.8 plugin host, so it must stay compatible with Python 3.8.

## Releases

Callers use the `v1` tag. After a change, move it to the new commit:

```sh
git tag -f v1 && git push -f origin v1
```

The reusable workflow checks out the action from its own commit, so the workflow and the action always match.
