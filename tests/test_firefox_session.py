import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, call

import pytest

from scripts import github_session as exporter
from utils import firefox_session
from utils.github_session import GitHubSessionError


@pytest.fixture
def firefox_driver(monkeypatch):
	driver = MagicMock()
	driver.current_url = 'https://github.com/settings/profile'
	driver.find_elements.return_value = [Mock(get_attribute=Mock(return_value='test-user'))]
	driver.get_cookies.return_value = [
		{
			'name': 'user_session',
			'value': 'private-firefox-session',
			'domain': 'github.com',
			'path': '/',
			'httpOnly': True,
			'secure': True,
			'sameSite': 'Lax',
		}
	]
	monkeypatch.setattr(firefox_session, '_launch_firefox', Mock(return_value=driver))
	monkeypatch.setattr('builtins.input', Mock(return_value=''))
	return driver


def test_firefox_export_verifies_login_and_normalizes_session_cookie(firefox_driver, capsys):
	cookies = firefox_session.capture_firefox_github_session('AgentRouter')

	assert cookies == [{**firefox_driver.get_cookies.return_value[0], 'expires': -1}]
	assert firefox_driver.get.call_args_list == [
		call('https://github.com/login'),
		call('https://github.com/settings/profile'),
	]
	firefox_driver.find_elements.assert_called_once_with('css selector', 'meta[name="user-login"]')
	firefox_driver.quit.assert_called_once()
	assert 'private-firefox-session' not in capsys.readouterr().out


def test_firefox_export_preserves_expiry_and_filters_foreign_cookies(firefox_driver):
	github_cookie = firefox_driver.get_cookies.return_value[0]
	github_cookie['expiry'] = 4_000_000_000
	github_cookie['extra'] = 'not-exported'
	firefox_driver.get_cookies.return_value.append({**github_cookie, 'domain': 'github.com.example.org'})

	cookies = firefox_session.capture_firefox_github_session('AgentRouter')

	assert len(cookies) == 1
	assert cookies[0]['expires'] == 4_000_000_000
	assert 'expiry' not in cookies[0]
	assert 'extra' not in cookies[0]


@pytest.mark.parametrize(
	'url',
	[
		'https://github.com/login',
		'https://github.com/sessions/two-factor',
		'http://github.com/settings/profile',
		'https://github.com.example.org/settings/profile',
	],
)
def test_firefox_export_rejects_unverified_login(firefox_driver, url):
	firefox_driver.current_url = url

	with pytest.raises(GitHubSessionError, match='not verified'):
		firefox_session.capture_firefox_github_session('AgentRouter')

	firefox_driver.get_cookies.assert_not_called()
	firefox_driver.quit.assert_called_once()


@pytest.mark.parametrize('users', [[], [Mock(get_attribute=Mock(return_value=''))]])
def test_firefox_export_requires_authenticated_user_metadata(firefox_driver, users):
	firefox_driver.find_elements.return_value = users

	with pytest.raises(GitHubSessionError, match='did not confirm'):
		firefox_session.capture_firefox_github_session('AgentRouter')

	firefox_driver.get_cookies.assert_not_called()
	firefox_driver.quit.assert_called_once()


def test_firefox_export_rejects_expired_cookie(firefox_driver):
	firefox_driver.get_cookies.return_value[0]['expiry'] = 1

	with pytest.raises(GitHubSessionError, match='expired'):
		firefox_session.capture_firefox_github_session('AgentRouter')

	firefox_driver.quit.assert_called_once()


def test_firefox_errors_never_echo_sensitive_driver_details(firefox_driver):
	firefox_driver.get_cookies.side_effect = RuntimeError('private-firefox-session')

	with pytest.raises(GitHubSessionError) as error:
		firefox_session.capture_firefox_github_session('AgentRouter')

	assert 'private-firefox-session' not in str(error.value)
	firefox_driver.quit.assert_called_once()


def test_firefox_is_closed_when_user_cancels(firefox_driver, monkeypatch):
	monkeypatch.setattr('builtins.input', Mock(side_effect=KeyboardInterrupt))

	with pytest.raises(KeyboardInterrupt):
		firefox_session.capture_firefox_github_session('AgentRouter')

	firefox_driver.get_cookies.assert_not_called()
	firefox_driver.quit.assert_called_once()


def test_firefox_cleanup_error_is_sanitized(firefox_driver):
	firefox_driver.quit.side_effect = RuntimeError('private-firefox-session')

	with pytest.raises(GitHubSessionError, match='cleanup failed') as error:
		firefox_session.capture_firefox_github_session('AgentRouter')

	assert 'private-firefox-session' not in str(error.value)


@pytest.mark.asyncio
async def test_firefox_selection_does_not_launch_or_download_cloakbrowser(monkeypatch):
	capture = Mock(return_value=[])
	cloakbrowser = AsyncMock()
	monkeypatch.setattr(exporter, 'capture_firefox_github_session', capture)
	monkeypatch.setattr(exporter, 'launch_login_context', cloakbrowser)

	assert await exporter.capture_github_session('AgentRouter', browser='firefox', use_proxy=True) == []

	capture.assert_called_once_with('AgentRouter', use_proxy=True)
	cloakbrowser.assert_not_awaited()


@pytest.mark.asyncio
async def test_export_command_passes_selected_browser(monkeypatch):
	monkeypatch.delenv('GITHUB_ACTIONS', raising=False)
	monkeypatch.setattr(exporter, 'check_secret_upload_access', Mock())
	capture = AsyncMock(return_value=[])
	monkeypatch.setattr(exporter, 'capture_github_session', capture)
	monkeypatch.setattr(exporter, 'encode_github_sessions', Mock(return_value='private-session-json'))
	upload = Mock()
	monkeypatch.setattr(exporter, 'upload_session_secret', upload)
	args = SimpleNamespace(
		account_name=['AgentRouter'],
		browser='firefox',
		use_proxy=False,
		repo='rzhli/anyrouter-check-in',
		environment='production',
	)

	await exporter.run(args)

	capture.assert_awaited_once_with('AgentRouter', use_proxy=False, browser='firefox')
	upload.assert_called_once_with('private-session-json', 'rzhli/anyrouter-check-in', 'production')


def test_firefox_proxy_is_disabled_by_default(monkeypatch):
	monkeypatch.setenv('CHECKIN_PROXY_URL', 'http://127.0.0.1:7890')
	options = Mock()

	firefox_session._configure_proxy(options, use_proxy=False)

	options.set_preference.assert_called_once_with('network.proxy.type', 0)


@pytest.mark.parametrize('scheme', ['http', 'socks5', 'socks5h'])
def test_firefox_proxy_preferences(monkeypatch, scheme):
	monkeypatch.setenv('CHECKIN_PROXY_URL', f'{scheme}://127.0.0.1:7890')
	options = Mock()

	firefox_session._configure_proxy(options, use_proxy=True)

	preferences = dict(arguments.args for arguments in options.set_preference.call_args_list)
	assert preferences['network.proxy.type'] == 1
	if scheme == 'http':
		assert preferences['network.proxy.http'] == '127.0.0.1'
		assert preferences['network.proxy.http_port'] == 7890
		assert preferences['network.proxy.ssl'] == '127.0.0.1'
		assert preferences['network.proxy.ssl_port'] == 7890
	else:
		assert preferences['network.proxy.socks'] == '127.0.0.1'
		assert preferences['network.proxy.socks_port'] == 7890
		assert preferences['network.proxy.socks_version'] == 5
		assert preferences['network.proxy.socks_remote_dns'] is True


@pytest.mark.parametrize(
	'url',
	[
		'',
		'http://127.0.0.1',
		'http://127.0.0.1:bad',
		'http://127.0.0.1:70000',
		'http://private:password@127.0.0.1:7890',
		'https://127.0.0.1:7890',
	],
)
def test_firefox_proxy_validation_does_not_echo_credentials(monkeypatch, url):
	monkeypatch.setenv('CHECKIN_PROXY_URL', url)

	with pytest.raises(GitHubSessionError) as error:
		firefox_session._configure_proxy(Mock(), use_proxy=True)

	assert 'private:password' not in str(error.value)


@pytest.fixture
def selenium_modules(monkeypatch):
	webdriver = Mock()
	options = Mock()
	monkeypatch.setitem(sys.modules, 'selenium', SimpleNamespace(webdriver=webdriver))
	monkeypatch.setitem(
		sys.modules, 'selenium.webdriver.firefox.options', SimpleNamespace(Options=Mock(return_value=options))
	)
	monkeypatch.setattr(firefox_session.shutil, 'which', Mock(return_value='/usr/bin/firefox'))
	return webdriver, options


def test_firefox_launch_uses_system_binary_without_existing_profile(selenium_modules):
	webdriver, options = selenium_modules

	assert firefox_session._launch_firefox(use_proxy=False) is webdriver.Firefox.return_value

	assert options.binary_location == '/usr/bin/firefox'
	assert options.page_load_strategy == 'eager'
	webdriver.Firefox.assert_called_once_with(options=options)


def test_firefox_launch_reports_missing_optional_dependency(monkeypatch):
	monkeypatch.setitem(sys.modules, 'selenium', None)

	with pytest.raises(GitHubSessionError, match='--extra firefox'):
		firefox_session._launch_firefox(use_proxy=False)


def test_firefox_launch_reports_missing_system_firefox(selenium_modules, monkeypatch):
	webdriver, options = selenium_modules
	monkeypatch.setattr(firefox_session.shutil, 'which', Mock(return_value=None))

	with pytest.raises(GitHubSessionError, match='not installed'):
		firefox_session._launch_firefox(use_proxy=False)

	webdriver.Firefox.assert_not_called()


def test_firefox_launch_failure_reports_driver_guidance_without_private_details(selenium_modules):
	webdriver, options = selenium_modules
	webdriver.Firefox.side_effect = RuntimeError('private-firefox-session')

	with pytest.raises(GitHubSessionError, match='geckodriver') as error:
		firefox_session._launch_firefox(use_proxy=False)

	assert 'private-firefox-session' not in str(error.value)
