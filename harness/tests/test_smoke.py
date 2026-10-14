"""
Generic smoke test for LSP packages.

The action validates the `smoke.yaml` of each fixture and writes them to `smoke_config.json` next to this package,
together with the paths of copies of the fixture directories. This module creates one test case per fixture.
"""

from __future__ import annotations

from LSP.plugin import filename_to_uri
from LSP.plugin.core.registry import windows
from pathlib import Path
from typing import Any
from typing import ClassVar
from typing import Generator
from typing import TYPE_CHECKING
from unittesting import DeferrableTestCase
import itertools
import json
import re
import sublime

if TYPE_CHECKING:
    from LSP.plugin import Session
    from LSP.protocol import Diagnostic

PACKAGE_DIR = Path(__file__).parent.parent
# Also covers starting the server, which can include downloading or installing it.
DEFAULT_DIAGNOSTICS_TIMEOUT_S = 180
VIEW_LOAD_TIMEOUT_MS = 10_000
# Some servers take a while to shut down, for example flow on Windows. Together with the maximum diagnostics_timeout and
# VIEW_LOAD_TIMEOUT_MS, this stays below UnitTesting's limit of 300 seconds without test output.
SESSION_END_TIMEOUT_MS = 30_000
LOG_LINES = 200
SEVERITY_NAMES = {1: 'error', 2: 'warning', 3: 'information', 4: 'hint'}
# LSP logs protocol messages under this name instead of a server name.
PROTOCOL_LOG_NAME = ':'


def load_configs() -> list[dict[str, Any]]:
    configs = []
    for test in json.loads((PACKAGE_DIR / 'smoke_config.json').read_text(encoding='utf-8'))['tests']:
        fixture_dir = Path(test['fixture_dir'])
        smoke = test['smoke']
        smoke['name'] = test['name']
        smoke['fixture_dir'] = str(fixture_dir)
        smoke['file_path'] = str(fixture_dir / smoke['file'])
        configs.append(smoke)
    return configs


def diagnostic_matches(diagnostic: Diagnostic, expected: dict[str, Any]) -> bool:
    if 'source' in expected and diagnostic.get('source') != expected['source']:
        return False
    if 'code' in expected and str(diagnostic.get('code')) != str(expected['code']):
        return False
    if 'severity' in expected and diagnostic.get('severity') != expected['severity']:
        return False
    if 'message' in expected:
        message = diagnostic['message']
        # The message can be MarkupContent since LSP 3.18.
        text = message if isinstance(message, str) else message['value']
        if not re.search(expected['message'], text):
            return False
    return True


def format_diagnostic(diagnostic: Diagnostic) -> str:
    start = diagnostic['range']['start']
    message = diagnostic['message']
    text = message if isinstance(message, str) else message['value']
    severity = SEVERITY_NAMES.get(diagnostic.get('severity', 1), 'error')
    return (
        f'  {start["line"] + 1}:{start["character"] + 1} {severity} '
        f'source={diagnostic.get("source")!r} code={diagnostic.get("code")!r}: {text.splitlines()[0] if text else ""}'
    )


def format_log_line(name: str, message: str) -> str:
    return f'  {message}' if name == PROTOCOL_LOG_NAME else f'  {name}: {message}'


class SmokeTest(DeferrableTestCase):
    # Set on the subclass created for each fixture, see the end of this module.
    config: ClassVar[dict[str, Any]]

    def setUp(self) -> Generator[Any, None, None]:
        self.apply_settings()
        self.window = sublime.active_window()
        self.previous_project_data = self.window.project_data()
        # Use the fixture directory as the only folder, so that the server gets it as the workspace root and doesn't
        # pick up the configuration of any other directory.
        self.window.set_project_data({'folders': [{'path': self.config['fixture_dir']}]})
        self.view = self.window.open_file(self.config['file_path'])
        yield {'condition': lambda: not self.view.is_loading(), 'timeout': VIEW_LOAD_TIMEOUT_MS}
        if scope := self.config.get('syntax'):
            # For servers whose syntax isn't assigned to the file automatically, for example by its extension.
            syntaxes = sublime.find_syntax_by_scope(scope)
            if not syntaxes:
                self.fail(f'No syntax with the scope "{scope}" found. Install the package that provides it.')
            self.view.assign_syntax(syntaxes[0])

    def tearDown(self) -> Generator[Any, None, None]:
        try:
            session = self.find_session()
            if session:
                sublime.set_timeout_async(session.end_async)
                try:
                    yield {'condition': lambda: self.find_session() is None, 'timeout': SESSION_END_TIMEOUT_MS}
                except TimeoutError:
                    seconds = SESSION_END_TIMEOUT_MS // 1000
                    self.fail(f'Server "{self.config["server"]}" did not shut down within {seconds} seconds.')
        finally:
            if self.view.is_valid():
                self.view.set_scratch(True)
                self.view.close()
            self.window.set_project_data(self.previous_project_data or {})

    def apply_settings(self) -> None:
        # Settings have to be in place before the file is opened, since that starts the server. Cleanups run even when
        # setUp fails, unlike tearDown, so the settings never leak into the next test.
        self.previous_settings: dict[str, dict[str, tuple[bool, Any]]] = {}
        self.addCleanup(self.restore_settings)
        for name, values in self.config['settings'].items():
            settings = sublime.load_settings(name)
            self.previous_settings[name] = {key: (settings.has(key), settings.get(key)) for key in values}
            for key, value in values.items():
                settings.set(key, value)
            sublime.save_settings(name)

    def restore_settings(self) -> None:
        for name, previous in self.previous_settings.items():
            settings = sublime.load_settings(name)
            for key, (existed, value) in previous.items():
                if existed:
                    settings.set(key, value)
                else:
                    settings.erase(key)
            sublime.save_settings(name)

    def find_session(self) -> Session | None:
        wm = windows.lookup(self.window)
        if not wm:
            return None
        return wm.get_session(self.config['server'], self.config['file_path'])

    def diagnostics(self) -> list[Diagnostic]:
        # Check what the server reported rather than what LSP draws in the view, since the package is under test.
        session = self.find_session()
        if not session:
            return []
        return session.diagnostics.get_diagnostics_for_uri(filename_to_uri(self.config['file_path']))

    def failure_report(self, reason: str) -> str:
        lines = [reason]
        wm = windows.lookup(self.window)
        if wm:
            sessions = ', '.join(session.config.name for session in wm.get_sessions()) or 'none'
            # The log is only flushed to the panel when the panel is open, so read the buffered messages directly.
            log = getattr(wm, '_server_log', [])[-LOG_LINES:]
            diagnostics = self.diagnostics()
            lines.extend((f'Sessions in window: {sessions}', f'Diagnostics received: {len(diagnostics)}'))
            lines.extend(map(format_diagnostic, diagnostics))
            lines.append(f'Last {len(log)} server log lines:')
            lines.extend(itertools.starmap(format_log_line, log))
        return '\n'.join(lines)

    def test_server(self) -> Generator[Any, None, None]:
        expected = self.config['expected_diagnostics']
        min_count = expected.get('min_count', 1)
        timeout = self.config.get('diagnostics_timeout', DEFAULT_DIAGNOSTICS_TIMEOUT_S)

        def matching() -> list[Diagnostic]:
            return [d for d in self.diagnostics() if diagnostic_matches(d, expected)]

        try:
            yield {'condition': lambda: len(matching()) >= min_count, 'timeout': timeout * 1000}
        except TimeoutError:
            if self.find_session():
                reason = (
                    f'Expected at least {min_count} diagnostic(s) matching {json.dumps(expected)} '
                    f'within {timeout} seconds, got {len(matching())}.'
                )
            else:
                reason = f'Server "{self.config["server"]}" did not start within {timeout} seconds.'
            self.fail(self.failure_report(reason))


# Create a test case for each fixture. The base class is removed from the module, so that it isn't run on its own.
for _config in load_configs():
    _name = 'Smoke_' + re.sub(r'\W', '_', _config['name'])
    globals()[_name] = type(_name, (SmokeTest,), {'config': _config})
del SmokeTest
