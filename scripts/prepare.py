"""
Resolve the action's inputs, validate the smoke.yaml files and prepare the fixtures.

Runs on the GitHub runner before Sublime Text is installed, so that configuration mistakes fail the job right away.
Writes the `package`, `lsp-package` and `package-path` step outputs, and `lsp-smoke-config.json` and
`lsp-smoke-fixtures` in the runner's temporary directory for install_harness.py.
"""

from __future__ import annotations

from pathlib import Path
from typing import NoReturn
import io
import os
import shutil
import subprocess
import sys
import tarfile
import venv

PYYAML_VERSION = '6.0.3'
SCRIPTS_DIR = Path(__file__).parent


def error(message: str) -> NoReturn:
    print(f'::error::{message}')
    sys.exit(1)


def venv_python(venv_dir: Path) -> Path:
    if sys.platform == 'win32':
        return venv_dir / 'Scripts' / 'python.exe'
    return venv_dir / 'bin' / 'python'


def export_package(package_path: str, dest: Path) -> str:
    """
    Export the committed files of the package checkout to `dest`, the way Package Control gets them, and return `dest`.

    Package Control installs the archive that GitHub, GitLab or Bitbucket generate for a release, which leaves out the
    files marked `export-ignore` in .gitattributes. A plain copy of the checkout would include them, so a package that
    excludes a file it needs would pass the test and break once released.
    """
    git_path = shutil.which('git')
    if not git_path:
        error('git not found')
    git = [git_path, '-C', package_path]
    if subprocess.run([*git, 'rev-parse', '--git-dir'], capture_output=True, check=False).returncode:
        error(f'package-path {package_path} is not a git checkout')
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    archive = subprocess.run([*git, 'archive', '--format=tar', 'HEAD'], capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        exported = {member.name for member in tar.getmembers() if not member.isdir()}
        tar.extractall(dest, filter='data')
    tracked = subprocess.run([*git, 'ls-files', '-z'], capture_output=True, check=True, encoding='utf-8').stdout
    if excluded := sorted(set(tracked.split('\0')) - exported - {''}):
        print(f'Left out by export-ignore, as in the release Package Control installs: {", ".join(excluded)}')
    # UnitTesting's setup only copies a package directory that contains .git, and removes it from the copy.
    subprocess.run([git_path, 'init', '--quiet', str(dest)], check=True)
    return str(dest)


def main() -> None:
    env = os.environ
    temp_dir = Path(env['RUNNER_TEMP'])
    package_path = env['INPUT_PACKAGE_PATH']
    install_package = env['INPUT_INSTALL_PACKAGE'] == 'true'
    # Without a package under test, every smoke.yaml has to name its session.
    package = env['INPUT_PACKAGE_NAME'] or (env['GITHUB_REPOSITORY'].split('/', 1)[1] if install_package else '')
    # Without a ref, Package Control installs the latest release of LSP compatible with the Sublime Text build.
    lsp_package = f'sublimelsp/LSP@{env["INPUT_LSP_REF"]}' if env['INPUT_LSP_REF'] else 'LSP'
    # An absolute fixture-dir is used as is, since joining an absolute path replaces the base.
    lines = [line.strip() for line in env['INPUT_FIXTURE_DIR'].splitlines() if line.strip()]
    fixture_srcs = [Path(package_path, line).resolve() for line in lines]
    names = [path.name for path in fixture_srcs]
    if duplicates := sorted({name for name in names if names.count(name) > 1}):
        error(f'fixture directories must have different names: {duplicates}')
    print(f'Testing {package or "packages"} against {lsp_package}', flush=True)

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

    # Install the package from its committed files without the ones marked export-ignore, like Package Control would.
    if install_package:
        package_path = export_package(package_path, temp_dir / 'lsp-smoke-package' / (package or 'package'))

    with Path(env['GITHUB_OUTPUT']).open('a', encoding='utf-8') as output:
        output.write(f'package={package}\nlsp-package={lsp_package}\npackage-path={package_path}\n')


if __name__ == '__main__':
    main()
