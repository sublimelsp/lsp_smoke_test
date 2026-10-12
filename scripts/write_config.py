"""
Validate the package's smoke.yaml and write it as JSON for the harness.

Runs on the GitHub runner before Sublime Text starts, because Sublime Text's Python can't read YAML.

Usage: write_config.py <package name> <smoke.yaml> <fixture dir> <output json>
"""

from __future__ import annotations

from pathlib import Path
from typing import NoReturn
import json
import sys
import yaml

# Take the allowed keys from the schema that editors use, so that the two can't get out of sync.
SCHEMA = json.loads((Path(__file__).parent.parent / 'smoke.schema.json').read_text(encoding='utf-8'))
SMOKE_KEYS = set(SCHEMA['properties'])
EXPECT_KEYS = set(SCHEMA['definitions']['diagnostics']['properties'])


def error(message: str) -> NoReturn:
    print(f'::error::{message}')
    sys.exit(1)


def main() -> None:
    package, smoke_file, fixture_dir, output = sys.argv[1:]
    smoke = yaml.safe_load(Path(smoke_file).read_text(encoding='utf-8'))
    if not isinstance(smoke, dict):
        error(f'{smoke_file} must contain a mapping')
    if unknown := set(smoke) - SMOKE_KEYS:
        error(f'{smoke_file}: unknown keys {sorted(unknown)}')
    if 'file' not in smoke:
        error(f'{smoke_file}: "file" is required')
    if not (Path(fixture_dir) / smoke['file']).is_file():
        error(f'{smoke_file}: file "{smoke["file"]}" not found in the fixture directory')
    expect = smoke.get('expect', {'min_diagnostics': 1})
    if not isinstance(expect, dict):
        error(f'{smoke_file}: "expect" must be a mapping')
    if unknown := set(expect) - EXPECT_KEYS:
        error(f'{smoke_file}: unknown keys in "expect" {sorted(unknown)}')
    smoke['expect'] = expect
    smoke.setdefault('server', package)
    config = {'fixture_dir': fixture_dir, 'smoke': smoke}
    Path(output).write_text(json.dumps(config, indent=2), encoding='utf-8')
    print(json.dumps(smoke, indent=2))


if __name__ == '__main__':
    main()
