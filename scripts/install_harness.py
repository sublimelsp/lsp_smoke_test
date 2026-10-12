"""
Install the smoke test harness, its configuration and any local libraries into Sublime Text.

Runs after UnitTesting's setup action, which sets SUBLIME_TEXT_PACKAGES and has Package Control install the libraries
that the packages depend on.
"""

from __future__ import annotations

from pathlib import Path
from typing import NoReturn
import json
import os
import shutil
import sys

HARNESS_NAME = 'LSPSmokeTest'
ACTION_DIR = Path(__file__).parent.parent


def error(message: str) -> NoReturn:
    print(f'::error::{message}')
    sys.exit(1)


def install_local_library(lib_dir: Path, name: str, source: Path) -> None:
    if not source.is_dir():
        error(f'local library {name}: directory {source} not found')
    # Replace the library for every Python version that Package Control installed it for.
    targets = [path / name for path in sorted(lib_dir.glob('python3*')) if (path / name).is_dir()]
    if not targets:
        error(f'local library {name}: not installed in {lib_dir}, add a package that depends on it')
    for target in targets:
        print(f'Replacing {target} with {source}')
        shutil.rmtree(target)
        shutil.copytree(source, target, ignore=shutil.ignore_patterns('__pycache__'))


def main() -> None:
    # On Windows, Git Bash has already converted this from a path like /c/st/Data/Packages/.
    packages = Path(os.environ['SUBLIME_TEXT_PACKAGES'])
    harness = packages / HARNESS_NAME
    shutil.copytree(ACTION_DIR / 'harness', harness, dirs_exist_ok=True)
    shutil.copyfile(Path(os.environ['RUNNER_TEMP'], 'lsp-smoke-config.json'), harness / 'smoke_config.json')
    # Print LSP's debug messages to the console, which UnitTesting includes in the job log.
    settings = packages / 'User' / 'LSP.sublime-settings'
    if not settings.exists():
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text(json.dumps({'log_debug': True}), encoding='utf-8')
    for line in os.environ.get('INPUT_LOCAL_LIBRARIES', '').splitlines():
        if not line.strip():
            continue
        name, separator, source = line.partition('=')
        if not separator:
            error(f'local-libraries: expected "name=path", got "{line}"')
        install_local_library(packages.parent / 'Lib', name.strip(), Path(source.strip()).resolve())


if __name__ == '__main__':
    main()
