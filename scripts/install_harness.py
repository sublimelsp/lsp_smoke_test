"""
Install the smoke test harness and its configuration into Sublime Text.

Runs after UnitTesting's setup action, which sets SUBLIME_TEXT_PACKAGES.
"""

from __future__ import annotations

from pathlib import Path
import json
import os
import shutil

HARNESS_NAME = 'LSPSmokeTest'
ACTION_DIR = Path(__file__).parent.parent


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


if __name__ == '__main__':
    main()
