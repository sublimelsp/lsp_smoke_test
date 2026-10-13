"""
Validate the smoke.yaml of each fixture and write them as JSON for the harness.

Runs on the GitHub runner before Sublime Text starts, because Sublime Text's Python can't read YAML.

Usage: write_config.py <package name, or '' when there is no package under test> <output json> <fixture dir>...
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from typing import NoReturn
import json
import sys
import yaml

# Take the allowed keys from the schema that editors use, so that the two can't get out of sync.
SCHEMA = json.loads((Path(__file__).parent.parent / 'smoke.schema.json').read_text(encoding='utf-8'))
SMOKE_KEYS = set(SCHEMA['properties'])
EXPECTED_KEYS = set(SCHEMA['definitions']['diagnostics']['properties'])
TIMEOUT_SCHEMA = SCHEMA['properties']['diagnostics_timeout']
SETTINGS_SUFFIX = '.sublime-settings'


def error(message: str) -> NoReturn:
    print(f'::error::{message}')
    sys.exit(1)


def find_smoke_file(fixture_dir: Path) -> Path:
    for name in ('smoke.yaml', 'smoke.yml'):
        if (path := fixture_dir / name).is_file():
            return path
    error(f'smoke.yaml not found in {fixture_dir}')


def check_timeout(smoke_file: Path, timeout: Any) -> None:
    low, high = TIMEOUT_SCHEMA['exclusiveMinimum'], TIMEOUT_SCHEMA['maximum']
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not low < timeout <= high:
        error(
            f'{smoke_file}: "diagnostics_timeout" must be a number greater than {low} and at most {high}, '
            'because UnitTesting stops the run after 300 seconds without test output'
        )


def load_smoke(package: str, fixture_dir: Path) -> dict[str, Any]:
    smoke_file = find_smoke_file(fixture_dir)
    smoke = yaml.safe_load(smoke_file.read_text(encoding='utf-8'))
    if not isinstance(smoke, dict):
        error(f'{smoke_file} must contain a mapping')
    if unknown := set(smoke) - SMOKE_KEYS:
        error(f'{smoke_file}: unknown keys {sorted(unknown)}')
    if 'file' not in smoke:
        error(f'{smoke_file}: "file" is required')
    if not (fixture_dir / smoke['file']).is_file():
        error(f'{smoke_file}: file "{smoke["file"]}" not found in the fixture directory')
    if 'server' not in smoke and not package:
        error(f'{smoke_file}: "server" is required when there is no package under test')
    expected = smoke.get('expected_diagnostics', {'min_count': 1})
    if not isinstance(expected, dict):
        error(f'{smoke_file}: "expected_diagnostics" must be a mapping')
    if unknown := set(expected) - EXPECTED_KEYS:
        error(f'{smoke_file}: unknown keys in "expected_diagnostics" {sorted(unknown)}')
    settings = smoke.get('settings', {})
    if not isinstance(settings, dict) or not all(
        name.endswith(SETTINGS_SUFFIX) and isinstance(values, dict) for name, values in settings.items()
    ):
        error(f'{smoke_file}: "settings" must map *{SETTINGS_SUFFIX} file names to mappings')
    if 'diagnostics_timeout' in smoke:
        check_timeout(smoke_file, smoke['diagnostics_timeout'])
    smoke['expected_diagnostics'] = expected
    smoke['settings'] = settings
    smoke.setdefault('server', package)
    return smoke


def main() -> None:
    package, output, *fixture_dirs = sys.argv[1:]
    tests: list[dict[str, Any]] = []
    for fixture_dir in map(Path, fixture_dirs):
        smoke = load_smoke(package, fixture_dir)
        print(f'{fixture_dir.name}: {json.dumps(smoke)}')
        tests.append({'name': fixture_dir.name, 'fixture_dir': str(fixture_dir), 'smoke': smoke})
    Path(output).write_text(json.dumps({'tests': tests}, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
