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
import re
import sys
import yaml

# Take the allowed keys from the schema that editors use, so that the two can't get out of sync.
SCHEMA = json.loads((Path(__file__).parent.parent / 'smoke.schema.json').read_text(encoding='utf-8'))
SMOKE_KEYS = set(SCHEMA['properties'])
EXPECTED_KEYS = set(SCHEMA['definitions']['diagnostics']['properties'])
HOVER_SCHEMA = SCHEMA['definitions']['hover']
FORMATTING_SCHEMA = SCHEMA['definitions']['formatting']
EXPECTED_TEST_KEYS = ('expected_diagnostics', 'expected_hover', 'expected_formatting')
TIMEOUT_KEYS = ('diagnostics_timeout', 'hover_timeout', 'formatting_timeout')
SETTINGS_SUFFIX = '.sublime-settings'


def error(message: str) -> NoReturn:
    print(f'::error::{message}')
    sys.exit(1)


def find_smoke_file(fixture_dir: Path) -> Path:
    for name in ('smoke.yaml', 'smoke.yml'):
        if (path := fixture_dir / name).is_file():
            return path
    error(f'smoke.yaml not found in {fixture_dir}')


def check_timeouts(smoke_file: Path, smoke: dict[str, Any]) -> None:
    for key in TIMEOUT_KEYS:
        if key not in smoke:
            continue
        timeout = smoke[key]
        schema = SCHEMA['properties'][key]
        low, high = schema['exclusiveMinimum'], schema['maximum']
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not low < timeout <= high:
            error(
                f'{smoke_file}: "{key}" must be a number greater than {low} and at most {high}, '
                'because UnitTesting stops the run after 300 seconds without test output'
            )


def check_hover(smoke_file: Path, file: Path, hover: Any) -> None:
    if not isinstance(hover, dict):
        error(f'{smoke_file}: "expected_hover" must be a mapping')
    if unknown := set(hover) - set(HOVER_SCHEMA['properties']):
        error(f'{smoke_file}: unknown keys in "expected_hover" {sorted(unknown)}')
    for key in HOVER_SCHEMA['required']:
        if key not in hover:
            error(f'{smoke_file}: "{key}" is required in "expected_hover"')
    at = hover['at']
    if not isinstance(at, str) or not at:
        error(f'{smoke_file}: "at" in "expected_hover" must be a non-empty string')
    if at not in file.read_text(encoding='utf-8'):
        error(f'{smoke_file}: text "{at}" of "expected_hover" not found in "{file.name}"')
    if 'match' in hover:
        check_regex(smoke_file, '"match" in "expected_hover"', hover['match'])


def check_formatting(smoke_file: Path, file: Path, formatting: Any) -> None:
    if not isinstance(formatting, dict):
        error(f'{smoke_file}: "expected_formatting" must be a mapping')
    if unknown := set(formatting) - set(FORMATTING_SCHEMA['properties']):
        error(f'{smoke_file}: unknown keys in "expected_formatting" {sorted(unknown)}')
    for key in FORMATTING_SCHEMA['required']:
        if key not in formatting:
            error(f'{smoke_file}: "{key}" is required in "expected_formatting"')
    result_name = formatting['result']
    if not isinstance(result_name, str) or not result_name:
        error(f'{smoke_file}: "result" in "expected_formatting" must be a non-empty string')
    result = file.parent / result_name
    if not result.is_file():
        error(f'{smoke_file}: file "{result_name}" of "expected_formatting" not found in the fixture directory')
    # Otherwise the test would pass without the server formatting anything.
    if read_normalized(result) == read_normalized(file):
        error(f'{smoke_file}: "{file.name}" is already formatted like "{result_name}" of "expected_formatting"')


def read_normalized(path: Path) -> str:
    return path.read_text(encoding='utf-8').replace('\r\n', '\n')


def check_regex(smoke_file: Path, name: str, pattern: Any) -> None:
    if not isinstance(pattern, str):
        error(f'{smoke_file}: {name} must be a string')
    try:
        re.compile(pattern)
    except re.error as e:
        error(f'{smoke_file}: {name} is not a valid regular expression: {e}')


def check_diagnostics(smoke_file: Path, expected: Any) -> None:
    if not isinstance(expected, dict):
        error(f'{smoke_file}: "expected_diagnostics" must be a mapping')
    if unknown := set(expected) - EXPECTED_KEYS:
        error(f'{smoke_file}: unknown keys in "expected_diagnostics" {sorted(unknown)}')


def check_expected(smoke_file: Path, file: Path, smoke: dict[str, Any]) -> None:
    if not any(key in smoke for key in EXPECTED_TEST_KEYS):
        keys = ', '.join(f'"{key}"' for key in EXPECTED_TEST_KEYS)
        error(f'{smoke_file}: set at least one of {keys}')
    if 'expected_diagnostics' in smoke:
        check_diagnostics(smoke_file, smoke['expected_diagnostics'])
    if 'expected_hover' in smoke:
        check_hover(smoke_file, file, smoke['expected_hover'])
    if 'expected_formatting' in smoke:
        check_formatting(smoke_file, file, smoke['expected_formatting'])


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
    if 'session_name' not in smoke and not package:
        error(f'{smoke_file}: "session_name" is required when there is no package under test')
    check_expected(smoke_file, fixture_dir / smoke['file'], smoke)
    settings = smoke.get('settings', {})
    if not isinstance(settings, dict) or not all(
        name.endswith(SETTINGS_SUFFIX) and isinstance(values, dict) for name, values in settings.items()
    ):
        error(f'{smoke_file}: "settings" must map *{SETTINGS_SUFFIX} file names to mappings')
    check_timeouts(smoke_file, smoke)
    smoke['settings'] = settings
    smoke.setdefault('session_name', package)
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
