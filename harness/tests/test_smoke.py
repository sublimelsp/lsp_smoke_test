"""
Generic smoke test for LSP packages.

The action validates the package's `smoke.yaml` and writes it as `smoke_config.json` next to this package, together
with the path of a copy of the package's fixture directory.
"""

from __future__ import annotations

from LSP.plugin.core.registry import windows
from LSP.plugin.core.types import ClientStates
from pathlib import Path
from typing import Any
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
DEFAULT_STARTUP_TIMEOUT_S = 120
DEFAULT_DIAGNOSTICS_TIMEOUT_S = 30
VIEW_LOAD_TIMEOUT_MS = 10_000
SESSION_END_TIMEOUT_MS = 10_000
LOG_LINES = 200
STATE_NAMES = {ClientStates.STARTING: 'starting', ClientStates.READY: 'ready', ClientStates.STOPPING: 'stopping'}
SEVERITY_NAMES = {1: 'error', 2: 'warning', 3: 'information', 4: 'hint'}
# LSP logs protocol messages under this name instead of a server name.
PROTOCOL_LOG_NAME = ':'


def load_config() -> dict[str, Any]:
    config = json.loads((PACKAGE_DIR / 'smoke_config.json').read_text(encoding='utf-8'))
    fixture_dir = Path(config['fixture_dir'])
    smoke = config['smoke']
    smoke['fixture_dir'] = str(fixture_dir)
    smoke['file_path'] = str(fixture_dir / smoke['file'])
    return smoke


def diagnostic_matches(diagnostic: Diagnostic, expect: dict[str, Any]) -> bool:
    if 'source' in expect and diagnostic.get('source') != expect['source']:
        return False
    if 'code' in expect and str(diagnostic.get('code')) != str(expect['code']):
        return False
    if 'severity' in expect and diagnostic.get('severity') != expect['severity']:
        return False
    if 'message' in expect:
        message = diagnostic['message']
        # The message can be MarkupContent since LSP 3.18.
        text = message if isinstance(message, str) else message['value']
        if not re.search(expect['message'], text):
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
    def setUp(self) -> Generator[Any, None, None]:
        self.config = load_config()
        self.window = sublime.active_window()
        self.previous_project_data = self.window.project_data()
        # Use the fixture directory as the only folder, so that the server gets it as the workspace root and doesn't
        # pick up the configuration of any other directory.
        self.window.set_project_data({'folders': [{'path': self.config['fixture_dir']}]})
        self.view = self.window.open_file(self.config['file_path'])
        yield {'condition': lambda: not self.view.is_loading(), 'timeout': VIEW_LOAD_TIMEOUT_MS}

    def tearDown(self) -> Generator[Any, None, None]:
        session = self.find_session()
        if session:
            sublime.set_timeout_async(session.end_async)
            yield {'condition': lambda: self.find_session() is None, 'timeout': SESSION_END_TIMEOUT_MS}
        if self.view.is_valid():
            self.view.set_scratch(True)
            self.view.close()
        self.window.set_project_data(self.previous_project_data or {})

    def find_session(self) -> Session | None:
        wm = windows.lookup(self.window)
        if not wm:
            return None
        return wm.get_session(self.config['server'], self.config['file_path'])

    def is_ready(self) -> bool:
        session = self.find_session()
        return bool(session and session.state == ClientStates.READY and session.session_view_for_view_async(self.view))

    def diagnostics(self) -> list[Diagnostic]:
        session = self.find_session()
        session_view = session.session_view_for_view_async(self.view) if session else None
        if not session_view:
            return []
        return [diagnostic for diagnostic, _ in session_view.session_buffer.diagnostics]

    def failure_report(self, reason: str) -> str:
        lines = [reason]
        wm = windows.lookup(self.window)
        if wm:
            sessions = (
                ', '.join(f'{s.config.name} ({STATE_NAMES.get(s.state, s.state)})' for s in wm.get_sessions()) or 'none'
            )
            # The log is only flushed to the panel when the panel is open, so read the buffered messages directly.
            log = getattr(wm, '_server_log', [])[-LOG_LINES:]
            diagnostics = self.diagnostics()
            lines.extend((f'Sessions in window: {sessions}', f'Diagnostics received: {len(diagnostics)}'))
            lines.extend(map(format_diagnostic, diagnostics))
            lines.append(f'Last {len(log)} server log lines:')
            lines.extend(itertools.starmap(format_log_line, log))
        return '\n'.join(lines)

    def test_server(self) -> Generator[Any, None, None]:
        server = self.config['server']
        startup_timeout = self.config.get('startup_timeout', DEFAULT_STARTUP_TIMEOUT_S)
        try:
            yield {'condition': self.is_ready, 'timeout': startup_timeout * 1000}
        except TimeoutError:
            self.fail(self.failure_report(f'Server "{server}" did not start within {startup_timeout} seconds.'))

        expect = self.config['expect']
        if expect == 'start':
            return
        min_diagnostics = expect.get('min_diagnostics', 1)
        diagnostics_timeout = self.config.get('diagnostics_timeout', DEFAULT_DIAGNOSTICS_TIMEOUT_S)

        def matching() -> list[Diagnostic]:
            return [d for d in self.diagnostics() if diagnostic_matches(d, expect)]

        try:
            yield {'condition': lambda: len(matching()) >= min_diagnostics, 'timeout': diagnostics_timeout * 1000}
        except TimeoutError:
            self.fail(
                self.failure_report(
                    f'Expected at least {min_diagnostics} diagnostic(s) matching {json.dumps(expect)} '
                    f'within {diagnostics_timeout} seconds, got {len(matching())}.'
                )
            )
