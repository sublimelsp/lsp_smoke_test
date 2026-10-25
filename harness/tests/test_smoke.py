"""
Generic smoke test for LSP packages.

The action validates the `smoke.yaml` of each fixture and writes them to `smoke_config.json` next to this package,
together with the paths of copies of the fixture directories. This module creates one test case per fixture.
"""

from __future__ import annotations

from LSP.plugin import apply_text_edits
from LSP.plugin import filename_to_uri
from LSP.plugin import Request
from LSP.plugin import text_document_position_params
from LSP.plugin.core.registry import windows
from LSP.plugin.core.views import text_document_formatting
from pathlib import Path
from typing import Any
from typing import Callable
from typing import ClassVar
from typing import Generator
from typing import TYPE_CHECKING
from unittesting import DeferrableTestCase
import difflib
import itertools
import json
import re
import sublime
import time

# The asyncio version of LSP ends a session with a coroutine, which has to run on its event loop.
try:
    from LSP.plugin import run_coroutine  # pyright: ignore[reportAttributeAccessIssue]
except ImportError:
    run_coroutine = None

if TYPE_CHECKING:
    from LSP.plugin import Session
    from LSP.protocol import Diagnostic
    from LSP.protocol import Hover
    from LSP.protocol import MarkedString
    from LSP.protocol import MarkupContent
    from LSP.protocol import TextEdit

PACKAGE_DIR = Path(__file__).parent.parent
# Also covers starting the server, which can include downloading or installing it.
DEFAULT_DIAGNOSTICS_TIMEOUT_S = 180
# After the server has started.
DEFAULT_HOVER_TIMEOUT_S = 10
DEFAULT_FORMATTING_TIMEOUT_S = 10
# Servers can return no hover or edits while they are still processing the file, so requests are repeated.
REQUEST_RETRY_INTERVAL_S = 1
RESPONSE_REPORT_CHARS = 500
DIFF_REPORT_LINES = 50
VIEW_LOAD_TIMEOUT_MS = 10_000
SESSION_END_TIMEOUT_MS = 10_000
LOG_LINES = 200
SEVERITY_NAMES = {1: 'error', 2: 'warning', 3: 'information', 4: 'hint'}
# LSP logs protocol messages under this name instead of a server name.
PROTOCOL_LOG_NAME = ':'
# The smoke.yaml key that each test checks. A test is skipped when its key isn't set.
TEST_KEYS = {
    'test_diagnostics': 'expected_diagnostics',
    'test_hover': 'expected_hover',
    'test_formatting': 'expected_formatting',
}
# Dialogs that LSP shows, for example when a server fails to start or keeps crashing, and what the stubs that replace
# them while a test runs return: Cancel or No. Nobody answers a dialog on CI, so it would block the plugin host until
# UnitTesting stops the run, and the failure report would never be printed.
DIALOG_ANSWERS: dict[str, object] = {
    'ok_cancel_dialog': False,
    'yes_no_cancel_dialog': sublime.DIALOG_CANCEL,
    'message_dialog': None,
    'error_message': None,
}


def load_configs() -> list[dict[str, Any]]:
    configs = []
    for test in json.loads((PACKAGE_DIR / 'smoke_config.json').read_text(encoding='utf-8'))['tests']:
        fixture_dir = Path(test['fixture_dir'])
        smoke = test['smoke']
        smoke['name'] = test['name']
        smoke['fixture_dir'] = str(fixture_dir)
        smoke['file_path'] = str(fixture_dir / smoke['file'])
        if formatting := smoke.get('expected_formatting'):
            formatting['result_path'] = str(fixture_dir / formatting['result'])
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


def hover_text(contents: MarkupContent | MarkedString | list[MarkedString]) -> str:
    if isinstance(contents, list):
        return '\n'.join(map(hover_text, contents))
    # A MarkupContent or a MarkedString with a language.
    return contents if isinstance(contents, str) else contents['value']


def hover_matches(hover: Hover | None, expected: dict[str, Any]) -> bool:
    if not hover:
        return False
    text = hover_text(hover['contents'])
    if 'match' in expected:
        return re.search(expected['match'], text) is not None
    return bool(text.strip())


def read_normalized(path: str) -> str:
    return Path(path).read_text(encoding='utf-8').replace('\r\n', '\n')


def truncate(text: str) -> str:
    return text[:RESPONSE_REPORT_CHARS] + '...' if len(text) > RESPONSE_REPORT_CHARS else text


def format_diff(expected: str, actual: str) -> str:
    diff = list(difflib.unified_diff(expected.splitlines(), actual.splitlines(), 'expected', 'formatted', lineterm=''))
    if len(diff) > DIFF_REPORT_LINES:
        diff = [*diff[:DIFF_REPORT_LINES], f'... {len(diff) - DIFF_REPORT_LINES} more lines']
    return '\n'.join(f'  {line}' for line in diff)


def format_diagnostic(diagnostic: Diagnostic) -> str:
    start = diagnostic['range']['start']
    message = diagnostic['message']
    text = message if isinstance(message, str) else message['value']
    severity = SEVERITY_NAMES.get(diagnostic.get('severity', 1), 'error')
    return (
        f'  {start["line"] + 1}:{start["character"] + 1} {severity} '
        f'source={diagnostic.get("source")!r} code={diagnostic.get("code")!r}: {text.splitlines()[0] if text else ""}'
    )


def end_session(session: Session) -> None:
    if run_coroutine:
        run_coroutine(session.end())  # pyright: ignore[reportAttributeAccessIssue]
    else:
        sublime.set_timeout_async(session.end_async)


def format_log_line(name: str, message: str) -> str:
    return f'  {message}' if name == PROTOCOL_LOG_NAME else f'  {name}: {message}'


def format_dialog(function: str, message: str) -> str:
    return '\n'.join(f'  {line}' for line in f'{function}: {message}'.splitlines())


class SmokeTest(DeferrableTestCase):
    # Set on the subclass created for each fixture, see the end of this module.
    config: ClassVar[dict[str, Any]]

    def setUp(self) -> Generator[Any, None, None]:
        # Skip before opening the file, which starts the server, so that a skipped test doesn't leave a server starting.
        key = TEST_KEYS[self._testMethodName]
        if self.config.get(key) is None:
            self.skipTest(f'No {key} configured.')
        self.stub_dialogs()
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
            # Close the view first, since LSP starts the servers again for an open view when their sessions end.
            if self.view.is_valid():
                self.view.set_scratch(True)
                self.view.close()
            # A session can only be ended once it has started, which can take long, for example when the server is
            # installed first, so wait for that before giving the sessions the shorter time to shut down.
            timeout = self.config.get('diagnostics_timeout', DEFAULT_DIAGNOSTICS_TIMEOUT_S)
            try:
                yield {'condition': lambda: not self.session_starting(), 'timeout': timeout * 1000}
            except TimeoutError:
                self.fail(self.failure_report(f'Servers did not finish starting within {timeout} seconds.'))
            # End every session in the window, not only the one under test. A session that is left running, for example
            # one of another server that was still starting, can affect the next test: when it exits, LSP removes the
            # session view of a newer session with the same name from the views.
            try:
                yield {'condition': self.end_all_sessions, 'timeout': SESSION_END_TIMEOUT_MS}
            except TimeoutError:
                seconds = SESSION_END_TIMEOUT_MS // 1000
                reason = f'Servers did not shut down within {seconds} seconds.'
                self.fail(self.failure_report(reason))
        finally:
            self.window.set_project_data(self.previous_project_data or {})

    def stub_dialogs(self) -> None:
        # LSP and lsp_utils call the dialogs as attributes of the sublime module, which all plugins share, rather than
        # importing them, so replacing them in the module is enough. Restored by a cleanup, which runs after tearDown.
        self.dialogs: list[tuple[str, str]] = []
        for function, answer in DIALOG_ANSWERS.items():

            def stub(message: str, *_: object, function: str = function, answer: object = answer, **__: object) -> Any:
                self.dialogs.append((function, message))
                return answer

            self.addCleanup(setattr, sublime, function, getattr(sublime, function))
            setattr(sublime, function, stub)

    def wait(
        self, condition: Callable[[], bool], timeout: float, reason: Callable[[], str]
    ) -> Generator[Any, None, None]:
        """
        Wait up to `timeout` seconds for `condition`, and fail with `reason` if it isn't met.

        Fail right away when LSP shows a dialog instead, since it has then given up on the server, for example after it
        crashed several times or failed to start.
        """
        met = False

        def check() -> bool:
            nonlocal met
            met = condition()
            return met or bool(self.dialogs)

        try:
            yield {'condition': check, 'timeout': timeout * 1000}
        except TimeoutError:
            self.fail(self.failure_report(reason()))
        if not met:
            self.fail(self.failure_report('LSP showed a dialog, so the server failed to start or crashed.'))

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

    def end_all_sessions(self) -> bool:
        """End the sessions in the window and return whether all of them have ended."""
        wm = windows.lookup(self.window)
        if not wm:
            return True
        sessions = list(wm.get_sessions())
        for session in sessions:
            if not session.exiting:
                end_session(session)
        return not sessions and not self.session_starting()

    def session_starting(self) -> bool:
        # A session that is still starting isn't in the window's sessions yet.
        wm = windows.lookup(self.window)
        if wm is None:
            return False
        if is_session_starting := getattr(wm, 'is_session_starting', None):
            return is_session_starting()
        # LSP releases before is_session_starting() was added. While sessions are started for a view, its listener is
        # set and other views wait in the queue.
        if (pending_listeners := getattr(wm, '_pending_listeners', None)) is not None:
            return getattr(wm, '_new_listener', None) is not None or bool(pending_listeners)
        # The asyncio version of LSP starts sessions one at a time, with a lock.
        start_lock = getattr(wm, '_start_lock', None)
        return start_lock is not None and start_lock.locked()

    def find_session(self) -> Session | None:
        wm = windows.lookup(self.window)
        if not wm:
            return None
        return wm.get_session(self.config['session_name'], self.config['file_path'])

    def diagnostics(self) -> list[Diagnostic]:
        # Check what the server reported rather than what LSP draws in the view, since the package is under test.
        session = self.find_session()
        if not session:
            return []
        return session.diagnostics.get_diagnostics_for_uri(filename_to_uri(self.config['file_path']))

    def failure_report(self, reason: str) -> str:
        lines = [reason]
        if self.dialogs:
            lines.append(f'Dialogs shown, answered with Cancel or No: {len(self.dialogs)}')
            lines.extend(itertools.starmap(format_dialog, self.dialogs))
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

    def test_diagnostics(self) -> Generator[Any, None, None]:
        expected = self.config['expected_diagnostics']
        min_count = expected.get('min_count', 1)
        timeout = self.config.get('diagnostics_timeout', DEFAULT_DIAGNOSTICS_TIMEOUT_S)

        def matching() -> list[Diagnostic]:
            return [d for d in self.diagnostics() if diagnostic_matches(d, expected)]

        def reason() -> str:
            if self.find_session():
                return (
                    f'Expected at least {min_count} diagnostic(s) matching {json.dumps(expected)} '
                    f'within {timeout} seconds, got {len(matching())}.'
                )
            return f'Session "{self.config["session_name"]}" did not start within {timeout} seconds.'

        yield from self.wait(lambda: len(matching()) >= min_count, timeout, reason)

    def request_until_matched(
        self, capability: str, request_async: Callable[[Session], None], timeout: float, failure: Callable[[], str]
    ) -> Generator[Any, None, None]:
        """
        Wait for the server to start, then repeat a request until its response matches.

        `request_async` is called every `REQUEST_RETRY_INTERVAL_S` on the worker thread, where LSP has to be called,
        with the session under test. It sends the request, and when it's done, sets `self.last_response`,
        `self.matched` and `self.request_pending = False`.
        """
        startup_timeout = self.config.get('diagnostics_timeout', DEFAULT_DIAGNOSTICS_TIMEOUT_S)
        # Set from the worker thread.
        self.session_ready = False
        self.request_pending = False
        self.request_sent_at = 0.0
        self.last_response: str | None = None
        self.matched = False

        def send_async() -> None:
            session = self.find_session()
            # The view is attached once LSP has sent the server the didOpen notification.
            if not session or not session.session_view_for_view_async(self.view):
                self.request_pending = False
                return
            self.session_ready = True
            if not session.has_capability(capability):
                self.last_response = f'the server does not have the {capability} capability'
                self.request_pending = False
                return
            request_async(session)

        def poll() -> bool:
            if self.matched:
                return True
            if not self.request_pending and time.monotonic() - self.request_sent_at >= REQUEST_RETRY_INTERVAL_S:
                self.request_pending = True
                self.request_sent_at = time.monotonic()
                sublime.set_timeout_async(send_async)
            return False

        yield from self.wait(
            lambda: poll() or self.session_ready,
            startup_timeout,
            lambda: f'Session "{self.config["session_name"]}" did not start within {startup_timeout} seconds.',
        )
        yield from self.wait(poll, timeout, failure)

    def on_error(self, error: Any) -> None:
        self.last_response = f'error: {error["message"]}'
        self.request_pending = False

    def test_hover(self) -> Generator[Any, None, None]:
        expected = self.config['expected_hover']
        region = self.view.find(expected['at'], 0, sublime.LITERAL)
        if region.a < 0:
            self.fail(f'Text "{expected["at"]}" not found in {self.config["file"]}.')
        point = region.a
        timeout = self.config.get('hover_timeout', DEFAULT_HOVER_TIMEOUT_S)

        def request_async(session: Session) -> None:
            def on_result(hover: Hover | None) -> None:
                self.last_response = json.dumps(hover)
                self.matched = hover_matches(hover, expected)
                self.request_pending = False

            params = text_document_position_params(self.view, point)
            session.send_request_async(Request('textDocument/hover', params, self.view), on_result, self.on_error)

        def failure() -> str:
            row, col = self.view.rowcol(point)
            return (
                f'Expected a hover matching {json.dumps(expected)} at {row + 1}:{col + 1} '
                f'within {timeout} seconds after the server started. '
                f'Last response: {truncate(self.last_response or "none")}'
            )

        yield from self.request_until_matched('hoverProvider', request_async, timeout, failure)

    def test_formatting(self) -> Generator[Any, None, None]:
        expected = self.config['expected_formatting']
        expected_text = read_normalized(expected['result_path'])
        timeout = self.config.get('formatting_timeout', DEFAULT_FORMATTING_TIMEOUT_S)

        def view_text() -> str:
            return self.view.substr(sublime.Region(0, self.view.size()))

        def request_async(session: Session) -> None:
            def on_applied(_: object) -> None:
                self.matched = view_text() == expected_text
                self.request_pending = False

            def on_result(edits: list[TextEdit] | None) -> None:
                self.last_response = json.dumps(edits)
                # Apply the edits like LSP does, rather than through its format command, which asks which server to
                # use when several can format the file.
                apply_text_edits(self.view, edits or []).then(on_applied)

            request = text_document_formatting(self.view)
            session.send_request_async(request, on_result, self.on_error)

        def failure() -> str:
            return (
                f'Expected {self.config["file"]} to be formatted like {expected["result"]} within {timeout} seconds '
                f'after the server started. Last response: {truncate(self.last_response or "none")}\n'
                f'Difference:\n{format_diff(expected_text, view_text())}'
            )

        yield from self.request_until_matched('documentFormattingProvider', request_async, timeout, failure)


# Create a test case for each fixture. The base class is removed from the module, so that it isn't run on its own.
for _config in load_configs():
    _name = 'Smoke_' + re.sub(r'\W', '_', _config['name'])
    globals()[_name] = type(_name, (SmokeTest,), {'config': _config})
del SmokeTest
