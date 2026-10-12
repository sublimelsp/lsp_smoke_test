"""
Resolve the action's inputs, validate smoke.yaml and prepare the fixture.

Runs on the GitHub runner before Sublime Text is installed, so that configuration mistakes fail the job right away.
Writes the `package` and `lsp-ref` step outputs, and `lsp-smoke-config.json` and `lsp-smoke-fixture` in the runner's
temporary directory for install_harness.py.
"""

from __future__ import annotations

from pathlib import Path
from typing import NoReturn
from urllib.request import Request
from urllib.request import urlopen
import json
import os
import shutil
import subprocess
import sys
import venv

PYYAML_VERSION = '6.0.3'
SCRIPTS_DIR = Path(__file__).parent


def error(message: str) -> NoReturn:
    print(f'::error::{message}')
    sys.exit(1)


def latest_lsp_release(token: str) -> str:
    request = Request('https://api.github.com/repos/sublimelsp/LSP/releases/latest')
    request.add_header('Accept', 'application/vnd.github+json')
    if token:
        request.add_header('Authorization', f'Bearer {token}')
    with urlopen(request, timeout=30) as response:
        return json.load(response)['tag_name']


def find_smoke_file(fixture_dir: Path) -> Path:
    for name in ('smoke.yaml', 'smoke.yml'):
        if (path := fixture_dir / name).is_file():
            return path
    error(f'smoke.yaml not found in {fixture_dir}')


def venv_python(venv_dir: Path) -> Path:
    if sys.platform == 'win32':
        return venv_dir / 'Scripts' / 'python.exe'
    return venv_dir / 'bin' / 'python'


def main() -> None:
    env = os.environ
    temp_dir = Path(env['RUNNER_TEMP'])
    package = env['INPUT_PACKAGE_NAME'] or env['GITHUB_REPOSITORY'].split('/', 1)[1]
    lsp_ref = env['INPUT_LSP_REF'] or latest_lsp_release(env['INPUT_GITHUB_TOKEN'])
    # An absolute fixture-dir is used as is, since joining an absolute path replaces the base.
    fixture_src = Path(env['INPUT_PACKAGE_PATH'], env['INPUT_FIXTURE_DIR']).resolve()
    smoke_file = find_smoke_file(fixture_src)
    print(f'Testing {package} against sublimelsp/LSP@{lsp_ref} with {smoke_file}', flush=True)

    # Copy the fixture out of the package checkout, so that the server doesn't find the package's own configuration
    # files (pyproject.toml, .editorconfig, ...) above it.
    fixture = temp_dir / 'lsp-smoke-fixture'
    shutil.rmtree(fixture, ignore_errors=True)
    shutil.copytree(fixture_src, fixture)

    # PyYAML isn't available on all runners, so install it into a separate environment.
    venv_dir = temp_dir / 'lsp-smoke-venv'
    venv.create(venv_dir, with_pip=True, clear=True)
    python = str(venv_python(venv_dir))
    subprocess.run(
        [python, '-m', 'pip', 'install', '--quiet', '--disable-pip-version-check', f'pyyaml=={PYYAML_VERSION}'],
        check=True,
    )
    result = subprocess.run(
        [
            python,
            '-B',
            str(SCRIPTS_DIR / 'write_config.py'),
            package,
            str(smoke_file),
            str(fixture),
            str(temp_dir / 'lsp-smoke-config.json'),
        ],
        check=False,
    )
    if result.returncode:
        sys.exit(result.returncode)

    with Path(env['GITHUB_OUTPUT']).open('a', encoding='utf-8') as output:
        output.write(f'package={package}\nlsp-ref={lsp_ref}\n')


if __name__ == '__main__':
    main()
