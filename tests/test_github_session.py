import os
import stat
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

import checkin
from scripts import github_session as exporter
from utils.github_session import (
	GitHubSessionError,
	encode_github_sessions,
	export_github_cookies,
	load_github_session,
	mask_github_cookie_values,
	parse_github_sessions,
)


@pytest.fixture(autouse=True)
def clean_session_environment(monkeypatch):
	monkeypatch.delenv('GITHUB_SESSION_STATE', raising=False)
	monkeypatch.delenv('GITHUB_ACTIONS', raising=False)


@pytest.fixture
def session_cookie():
	return {
		'name': 'user_session',
		'value': 'private-test-session',
		'domain': 'github.com',
		'path': '/',
		'expires': -1,
		'httpOnly': True,
		'secure': True,
		'sameSite': 'Lax',
	}


def test_named_session_round_trip_supports_multiple_accounts(session_cookie, monkeypatch):
	second_cookie = {**session_cookie, 'value': 'another-private-session'}
	raw = encode_github_sessions({'AgentRouter': [session_cookie], 'AgentRouter 2': [second_cookie]})
	monkeypatch.setenv('GITHUB_SESSION_STATE', raw)

	assert load_github_session('AgentRouter') == [session_cookie]
	assert load_github_session('AgentRouter 2') == [second_cookie]
	assert parse_github_sessions(raw) == {'AgentRouter': [session_cookie], 'AgentRouter 2': [second_cookie]}


def test_absent_secret_keeps_existing_login_flow():
	assert load_github_session('AgentRouter') is None


def test_session_account_name_must_match(monkeypatch, session_cookie):
	monkeypatch.setenv('GITHUB_SESSION_STATE', encode_github_sessions({'AgentRouter': [session_cookie]}))

	with pytest.raises(GitHubSessionError, match='account name'):
		load_github_session('Other name')


def test_export_excludes_other_sites_and_extra_cookie_fields(session_cookie):
	cookies = [
		{**session_cookie, 'extra': 'not-exported'},
		{**session_cookie, 'domain': '.agentrouter.org', 'value': 'provider-session'},
		{**session_cookie, 'domain': 'github.com.example.org', 'value': 'untrusted-session'},
	]

	assert export_github_cookies(cookies) == [session_cookie]


@pytest.mark.parametrize(
	'change',
	[
		{'domain': '.agentrouter.org'},
		{'domain': 'github.com.example.org'},
		{'secure': False},
		{'httpOnly': 'true'},
		{'path': 'invalid'},
		{'expires': 'forever'},
		{'expires': float('nan')},
		{'expires': float('inf')},
		{'sameSite': 'Invalid'},
		{'value': ''},
		{'value': 'private\nvalue'},
	],
)
def test_invalid_cookies_are_rejected_without_echoing_values(session_cookie, change):
	with pytest.raises(GitHubSessionError) as error:
		encode_github_sessions({'AgentRouter': [{**session_cookie, **change}]})
	assert 'private-test-session' not in str(error.value)


def test_expired_session_requires_manual_refresh(session_cookie):
	with pytest.raises(GitHubSessionError, match='expired'):
		encode_github_sessions({'AgentRouter': [{**session_cookie, 'expires': 1}]})


def test_expired_non_authentication_cookie_is_dropped(session_cookie):
	raw = encode_github_sessions(
		{'AgentRouter': [session_cookie, {**session_cookie, 'name': 'expired-extra', 'expires': 1}]}
	)

	assert parse_github_sessions(raw)['AgentRouter'] == [session_cookie]


def test_duplicate_cookie_records_are_rejected(session_cookie):
	with pytest.raises(GitHubSessionError, match='duplicate cookies'):
		encode_github_sessions({'AgentRouter': [session_cookie, session_cookie]})


@pytest.mark.parametrize(
	'raw',
	[
		'not-JSON-private-test-session',
		'[]',
		'{"version":2,"accounts":{}}',
		'{"version":true,"accounts":{}}',
		'{"version":1,"accounts":{}}',
		'{"version":1,"version":1,"accounts":{}}',
	],
)
def test_invalid_session_json_is_rejected_without_echoing_input(raw):
	with pytest.raises(GitHubSessionError) as error:
		parse_github_sessions(raw)
	assert 'private-test-session' not in str(error.value)


def test_oversized_secret_is_rejected():
	with pytest.raises(GitHubSessionError, match='size limit'):
		parse_github_sessions('x' * (49 * 1024))


def test_cookie_values_are_masked_only_in_actions(monkeypatch, session_cookie, capsys):
	mask_github_cookie_values([session_cookie])
	assert capsys.readouterr().out == ''
	monkeypatch.setenv('GITHUB_ACTIONS', 'true')
	mask_github_cookie_values([{**session_cookie, 'value': 'private%value'}])
	assert capsys.readouterr().out == '::add-mask::private%25value\n'


def test_upload_sends_secret_on_stdin_not_in_arguments(monkeypatch, session_cookie):
	payload = encode_github_sessions({'AgentRouter': [session_cookie]})
	command = Mock(return_value=SimpleNamespace(returncode=0))
	monkeypatch.setattr(exporter.subprocess, 'run', command)
	monkeypatch.setattr(exporter.shutil, 'which', lambda name: '/usr/bin/gh')

	exporter.upload_session_secret(payload, 'rzhli/anyrouter-check-in', 'production')

	assert command.call_args.args[0] == [
		'/usr/bin/gh',
		'secret',
		'set',
		'GITHUB_SESSION_STATE',
		'--repo',
		'rzhli/anyrouter-check-in',
		'--env',
		'production',
	]
	assert command.call_args.kwargs['input'] == payload
	assert command.call_args.kwargs['capture_output'] is True
	assert payload not in command.call_args.args[0]


def test_upload_errors_do_not_expose_gh_output(monkeypatch):
	monkeypatch.setattr(exporter.shutil, 'which', lambda name: '/usr/bin/gh')
	monkeypatch.setattr(
		exporter.subprocess, 'run', Mock(return_value=SimpleNamespace(returncode=1, stderr='private-session-value'))
	)

	with pytest.raises(GitHubSessionError) as error:
		exporter.upload_session_secret('private-session-value', 'owner/repository', 'production')
	assert 'private-session-value' not in str(error.value)


def test_upload_without_gh_reports_installation_instructions(monkeypatch):
	monkeypatch.setattr(exporter.shutil, 'which', lambda name: None)

	with pytest.raises(GitHubSessionError, match='gh auth login'):
		exporter.upload_session_secret('not-transmitted', 'owner/repository', 'production')


def test_private_file_is_owner_only_and_never_overwritten(tmp_path):
	path = tmp_path / '.session_exports' / 'session.json'
	exporter.write_private_session_file(path, 'private-session-value')

	assert stat.S_IMODE(path.stat().st_mode) == 0o600
	assert path.read_text() == 'private-session-value'
	with pytest.raises(GitHubSessionError, match='already exists'):
		exporter.write_private_session_file(path, 'replacement')
	assert path.read_text() == 'private-session-value'


@pytest.mark.skipif(os.name != 'posix', reason='Symlink behavior requires POSIX')
def test_private_file_refuses_symlink_destination(tmp_path):
	original = tmp_path / 'existing.json'
	original.write_text('original')
	link = tmp_path / 'link.json'
	link.symlink_to(original)

	with pytest.raises(GitHubSessionError):
		exporter.write_private_session_file(link, 'replacement')
	assert original.read_text() == 'original'


@pytest.mark.asyncio
@pytest.mark.parametrize('authenticated', [True, False])
async def test_export_verifies_github_and_closes_ephemeral_browser(monkeypatch, session_cookie, authenticated):
	page = MagicMock()
	page.url = 'https://github.com/settings/profile' if authenticated else 'https://github.com/login'
	page.goto = AsyncMock(return_value=SimpleNamespace(status=200))
	page.locator.return_value.get_attribute = AsyncMock(return_value='test-user')
	context = MagicMock()
	context.new_page = AsyncMock(return_value=page)
	context.cookies = AsyncMock(return_value=[session_cookie])
	context.close = AsyncMock()
	launch = AsyncMock(return_value=context)
	monkeypatch.setattr(exporter, 'launch_login_context', launch)
	monkeypatch.setattr(exporter.asyncio, 'to_thread', AsyncMock(return_value=''))

	if authenticated:
		assert await exporter.capture_github_session('AgentRouter') == [session_cookie]
		context.cookies.assert_awaited_once_with(['https://github.com/'])
	else:
		with pytest.raises(GitHubSessionError, match='not verified'):
			await exporter.capture_github_session('AgentRouter')
		context.cookies.assert_not_awaited()
	context.close.assert_awaited_once()
	assert launch.await_args is not None
	assert launch.await_args.args[0].headless is False
	assert launch.await_args.args[0].persist_profile is False


@pytest.mark.asyncio
async def test_export_tool_does_not_run_inside_actions(monkeypatch):
	monkeypatch.setenv('GITHUB_ACTIONS', 'true')
	with pytest.raises(GitHubSessionError, match='local desktop'):
		await exporter.run(exporter.argparse.Namespace())


@pytest.mark.asyncio
async def test_session_is_imported_before_agentrouter_logout(monkeypatch, session_cookie):
	from utils.config import AppConfig

	monkeypatch.delenv('PROVIDERS', raising=False)
	monkeypatch.setenv('GITHUB_SESSION_STATE', encode_github_sessions({'Test': [session_cookie]}))
	provider = AppConfig.load_from_env().providers['agentrouter']
	page = MagicMock()
	page.goto = AsyncMock()
	context = MagicMock()
	context.add_cookies = AsyncMock()
	context.new_page = AsyncMock(return_value=page)
	context.cookies = AsyncMock(return_value=[{'name': 'session', 'value': 'provider-session'}])
	context.close = AsyncMock()
	clear_state = AsyncMock()
	monkeypatch.setattr(checkin, 'launch_login_context', AsyncMock(return_value=context))
	monkeypatch.setattr(checkin, 'prepare_browser_page', AsyncMock())
	monkeypatch.setattr(checkin, 'navigate_login_page', AsyncMock())
	monkeypatch.setattr(checkin, 'clear_site_login_state', clear_state)
	monkeypatch.setattr(checkin, 'login_with_github_oauth', AsyncMock(return_value=page))
	monkeypatch.setattr(checkin, 'verify_browser_login', AsyncMock(return_value={'id': 99}))
	monkeypatch.setattr(checkin, 'save_login_screenshot', AsyncMock())
	pipeline = Mock()
	pipeline.attach_mock(context.add_cookies, 'import_session')
	pipeline.attach_mock(clear_state, 'logout')

	result = await checkin.login_with_credentials('Test', provider, 'agentrouter', auth_method='github')

	assert result is not None
	assert result.cookies == {'session': 'provider-session'}
	context.add_cookies.assert_awaited_once_with([session_cookie])
	assert [call[0] for call in pipeline.mock_calls] == ['import_session', 'logout']
	context.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_invalid_import_fails_without_starting_browser(monkeypatch):
	from utils.config import AppConfig

	monkeypatch.setenv('GITHUB_SESSION_STATE', 'invalid-private-session')
	monkeypatch.delenv('PROVIDERS', raising=False)
	launch = AsyncMock()
	monkeypatch.setattr(checkin, 'launch_login_context', launch)

	assert (
		await checkin.login_with_credentials(
			'Test', AppConfig.load_from_env().providers['agentrouter'], 'agentrouter', auth_method='github'
		)
		is None
	)
	launch.assert_not_awaited()


@pytest.mark.asyncio
async def test_cookie_import_exception_is_sanitized_and_browser_is_closed(monkeypatch, session_cookie, capsys):
	from utils.config import AppConfig

	monkeypatch.delenv('PROVIDERS', raising=False)
	monkeypatch.setenv('GITHUB_SESSION_STATE', encode_github_sessions({'Test': [session_cookie]}))
	context = MagicMock()
	context.add_cookies = AsyncMock(side_effect=ValueError('private-test-session'))
	context.close = AsyncMock()
	monkeypatch.setattr(checkin, 'launch_login_context', AsyncMock(return_value=context))

	result = await checkin.login_with_credentials(
		'Test', AppConfig.load_from_env().providers['agentrouter'], 'agentrouter', auth_method='github'
	)

	assert result is None
	context.close.assert_awaited_once()
	output = capsys.readouterr().out
	assert 'Cannot import GitHub session' in output
	assert 'private-test-session' not in output
