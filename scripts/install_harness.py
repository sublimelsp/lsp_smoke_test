"""
Install the smoke test harness and its configuration into Sublime Text.

Runs after UnitTesting's setup action, which sets SUBLIME_TEXT_PACKAGES.
"""

from __future__ import annotations

from pathlib import Path
import json
import os
import re
import shutil
import sys

HARNESS_NAME = 'LSPSmokeTest'
ACTION_DIR = Path(__file__).parent.parent


def packages_path() -> Path:
    path = os.environ['SUBLIME_TEXT_PACKAGES']
    # On Windows, UnitTesting sets a Git Bash path like /c/st/Data/Packages/.
    if sys.platform == 'win32' and (match := re.match(r'^/([a-zA-Z])/(.*)$', path)):
        path = f'{match.group(1)}:/{match.group(2)}'
    return Path(path)


def main() -> None:
    packages = packages_path()
    harness = packages / HARNESS_NAME
    shutil.copytree(ACTION_DIR / 'harness', harness, dirs_exist_ok=True)
    shutil.copyfile(Path(os.environ['RUNNER_TEMP'], 'lsp-smoke-config.json'), harness / 'smoke_config.json')
    # Keep server messages in the log so the test can print them when it fails.
    settings = packages / 'User' / 'LSP.sublime-settings'
    if not settings.exists():
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text(json.dumps({'log_debug': True, 'log_server': ['panel']}), encoding='utf-8')


if __name__ == '__main__':
    main()
