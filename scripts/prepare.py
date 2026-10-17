"""
Resolve the action's inputs, validate the smoke.yaml files and prepare the fixtures.

Runs on the GitHub runner before Sublime Text is installed, so that configuration mistakes fail the job right away.
Writes the `package` and `lsp-ref` step outputs, and `lsp-smoke-config.json` and `lsp-smoke-fixtures` in the runner's
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


def venv_python(venv_dir: Path) -> Path:
    if sys.platform == 'win32':
        return venv_dir / 'Scripts' / 'python.exe'
    return venv_dir / 'bin' / 'python'


def main() -> None:
    env = os.environ
    temp_dir = Path(env['RUNNER_TEMP'])
    package_path = env['INPUT_PACKAGE_PATH']
    install_package = env['INPUT_INSTALL_PACKAGE'] == 'true'
    # Without a package under test, every smoke.yaml has to name its session.
    package = env['INPUT_PACKAGE_NAME'] or (env['GITHUB_REPOSITORY'].split('/', 1)[1] if install_package else '')
    lsp_ref = env['INPUT_LSP_REF']
    if not lsp_ref:
        if lsp_ref := env.get('LSP_SMOKE_TEST_LSP_REF', ''):
            print(f'::notice::Testing against sublimelsp/LSP@{lsp_ref} from the LSP_SMOKE_TEST_LSP_REF variable.')
        else:
            lsp_ref = latest_lsp_release(env['INPUT_GITHUB_TOKEN'])
    # An absolute fixture-dir is used as is, since joining an absolute path replaces the base.
    lines = [line.strip() for line in env['INPUT_FIXTURE_DIR'].splitlines() if line.strip()]
    fixture_srcs = [Path(package_path, line).resolve() for line in lines]
    names = [path.name for path in fixture_srcs]
    if duplicates := sorted({name for name in names if names.count(name) > 1}):
        error(f'fixture directories must have different names: {duplicates}')
    print(f'Testing {package or "packages"} against sublimelsp/LSP@{lsp_ref}', flush=True)

    # Copy the fixtures out of the package checkout, so that the server doesn't find the package's own configuration
    # files (pyproject.toml, .editorconfig, ...) above them.
    fixtures_dir = temp_dir / 'lsp-smoke-fixtures'
    shutil.rmtree(fixtures_dir, ignore_errors=True)
    fixtures = []
    for src in fixture_srcs:
        if not src.is_dir():
            error(f'fixture directory {src} not found')
        shutil.copytree(src, fixtures_dir / src.name)
        fixtures.append(str(fixtures_dir / src.name))

    # PyYAML isn't available on all runners, so install it into a separate environment.
    venv_dir = temp_dir / 'lsp-smoke-venv'
    venv.create(venv_dir, with_pip=True, clear=True)
    python = str(venv_python(venv_dir))
    # Run Python outside the package checkout, since `python -m` puts the working directory on the module path, where a
    # module of the package such as types.py would shadow the standard library module of the same name.
    subprocess.run(
        [python, '-m', 'pip', 'install', '--quiet', '--disable-pip-version-check', f'pyyaml=={PYYAML_VERSION}'],
        check=True,
        cwd=temp_dir,
    )
    config = str(temp_dir / 'lsp-smoke-config.json')
    result = subprocess.run(
        [python, '-B', str(SCRIPTS_DIR / 'write_config.py'), package, config, *fixtures], check=False, cwd=temp_dir
    )
    if result.returncode:
        sys.exit(result.returncode)

    with Path(env['GITHUB_OUTPUT']).open('a', encoding='utf-8') as output:
        output.write(f'package={package}\nlsp-ref={lsp_ref}\n')


if __name__ == '__main__':
    main()
