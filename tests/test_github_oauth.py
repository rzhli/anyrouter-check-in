from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

import checkin
from utils import browser
from utils.browser import BrowserLoginResult
from utils.config import AccountConfig, AppConfig


def make_page(url: str) -> MagicMock:
	page = MagicMock()
	page.url = url
	page.is_closed.return_value = False
	return page


@pytest.fixture
def oauth_page(monkeypatch):
	page = make_page('https://agentrouter.org/login')
	page.context = SimpleNamespace(pages=[page])
	monkeypatch.setattr(browser, '_dismiss_blocking_overlays', AsyncMock())
	monkeypatch.setattr(browser, '_click_github_login_entry', AsyncMock(return_value=True))
	page.fill_credentials = AsyncMock(return_value=False)
	monkeypatch.setattr(browser, '_fill_github_credentials_if_needed', page.fill_credentials)
	monkeypatch.setattr(browser, '_authorize_github_oauth_if_needed', AsyncMock(return_value=False))
	monkeypatch.setattr(browser.asyncio, 'sleep', AsyncMock())
	return page


@pytest.mark.asyncio
async def test_oauth_direct_callback_returns_original_page(monkeypatch, oauth_page):
	async def click_entry(page):
		page.url = 'https://agentrouter.org/console'
		return True

	monkeypatch.setattr(browser, '_click_github_login_entry', click_entry)

	assert await browser.login_with_github_oauth(oauth_page) is oauth_page
	oauth_page.fill_credentials.assert_not_awaited()


@pytest.mark.asyncio
async def test_oauth_popup_callback_returns_authenticated_popup(monkeypatch, oauth_page):
	popup = make_page('https://github.com/login/oauth/authorize')

	async def click_entry(page):
		page.context.pages.append(popup)
		return True

	async def authorize(page):
		page.url = 'https://agentrouter.org/console'
		return True

	monkeypatch.setattr(browser, '_click_github_login_entry', click_entry)
	monkeypatch.setattr(browser, '_authorize_github_oauth_if_needed', AsyncMock(side_effect=authorize))

	assert await browser.login_with_github_oauth(oauth_page, 'test-user', 'test-password') is popup
	oauth_page.fill_credentials.assert_awaited_once()
	assert oauth_page.fill_credentials.await_args.args[:3] == (
		popup,
		'test-user',
		'test-password',
	)


@pytest.mark.asyncio
async def test_closed_oauth_popup_returns_original_page_for_verification(monkeypatch, oauth_page):
	popup = make_page('https://github.com/login/oauth/authorize')

	async def click_entry(page):
		page.context.pages.append(popup)
		return True

	async def authorize(page):
		page.is_closed.return_value = True
		return True

	monkeypatch.setattr(browser, '_click_github_login_entry', click_entry)
	monkeypatch.setattr(browser, '_authorize_github_oauth_if_needed', authorize)

	assert await browser.login_with_github_oauth(oauth_page) is oauth_page


@pytest.mark.asyncio
async def test_oauth_ignores_preexisting_github_tabs(monkeypatch, oauth_page):
	existing = make_page('https://github.com/settings/profile')
	oauth_page.context.pages.insert(0, existing)

	async def click_entry(page):
		page.url = 'https://agentrouter.org/console'
		return True

	monkeypatch.setattr(browser, '_click_github_login_entry', click_entry)

	assert await browser.login_with_github_oauth(oauth_page) is oauth_page
	oauth_page.fill_credentials.assert_not_awaited()


@pytest.mark.asyncio
async def test_oauth_waits_for_form_and_submits_credentials_only_once(monkeypatch, oauth_page):
	async def click_entry(page):
		page.url = 'https://github.com/login'
		return True

	async def finish_callback(delay):
		if wait.await_count == 3:
			oauth_page.url = 'https://agentrouter.org/console'

	fill_credentials = AsyncMock(side_effect=[False, True])
	wait = AsyncMock(side_effect=finish_callback)
	monkeypatch.setattr(browser, '_click_github_login_entry', click_entry)
	monkeypatch.setattr(browser, '_fill_github_credentials_if_needed', fill_credentials)
	monkeypatch.setattr(browser.asyncio, 'sleep', wait)

	assert await browser.login_with_github_oauth(oauth_page, 'test-user', 'test-password') is oauth_page
	assert fill_credentials.await_count == 2


@pytest.mark.asyncio
async def test_oauth_missing_entry_fails(monkeypatch, oauth_page):
	monkeypatch.setattr(browser, '_click_github_login_entry', AsyncMock(return_value=False))

	with pytest.raises(TimeoutError, match='Cannot find GitHub'):
		await browser.login_with_github_oauth(oauth_page)


@pytest.mark.asyncio
async def test_oauth_timeout_fails_instead_of_reporting_success(monkeypatch, oauth_page):
	monkeypatch.setattr(browser, 'time', SimpleNamespace(monotonic=Mock(side_effect=[0, 1])))

	with pytest.raises(TimeoutError, match='GitHub OAuth login did not complete'):
		await browser.login_with_github_oauth(oauth_page, timeout_ms=1)


@pytest.mark.asyncio
@pytest.mark.parametrize(
	'url',
	[
		'https://github.com.example.com/login',
		'https://evil.example/login?next=github.com',
		'https://github.com@evil.example/login',
		'http://github.com/login',
	],
)
async def test_github_credentials_and_authorization_require_trusted_origin(monkeypatch, url):
	page = make_page(url)
	locator = AsyncMock()
	monkeypatch.setattr(browser, '_first_visible_locator', locator)

	assert await browser._fill_github_credentials_if_needed(page, 'test-user', 'test-password', 100) is False
	assert await browser._authorize_github_oauth_if_needed(page) is False
	locator.assert_not_awaited()


@pytest.mark.asyncio
async def test_github_login_form_is_filled_and_submitted(monkeypatch):
	page = make_page('https://github.com/login')
	username_input, password_input, submit = MagicMock(), MagicMock(), MagicMock()
	set_value = AsyncMock()
	click = AsyncMock(return_value=True)
	monkeypatch.setattr(
		browser, '_first_visible_locator', AsyncMock(side_effect=[username_input, password_input, submit])
	)
	monkeypatch.setattr(browser, '_set_input_value', set_value)
	monkeypatch.setattr(browser, '_click_locator', click)

	assert await browser._fill_github_credentials_if_needed(page, 'test-user', 'test-password', 100) is True
	assert set_value.await_args_list[0].args == (username_input, 'test-user', 100)
	assert set_value.await_args_list[1].args == (password_input, 'test-password', 100)
	click.assert_awaited_once_with(submit)


@pytest.mark.asyncio
@pytest.mark.parametrize('logout_error', [None, TimeoutError('server unavailable')])
async def test_logout_clears_site_state_but_preserves_github_cookies(logout_error):
	page = make_page('https://agentrouter.org/login')
	page.context.request.get = AsyncMock(side_effect=logout_error)
	page.evaluate = AsyncMock()
	page.context.clear_cookies = AsyncMock()

	await browser.clear_site_login_state(page, 'https://agentrouter.org', 100)

	page.context.request.get.assert_awaited_once_with('https://agentrouter.org/api/user/logout', timeout=100)
	page.evaluate.assert_awaited_once()
	domain_filter = page.context.clear_cookies.await_args.kwargs['domain']
	assert domain_filter.search('agentrouter.org')
	assert domain_filter.search('.agentrouter.org')
	assert domain_filter.search('api.agentrouter.org')
	assert not domain_filter.search('.github.com')
	assert not domain_filter.search('otheragentrouter.org')
	assert not domain_filter.search('agentrouter.org.example.com')


@pytest.mark.asyncio
async def test_logout_refuses_to_clear_another_origin():
	page = make_page('https://github.com/login')
	page.evaluate = AsyncMock()
	page.context.clear_cookies = AsyncMock()

	with pytest.raises(ValueError, match='provider origin'):
		await browser.clear_site_login_state(page, 'https://agentrouter.org', 100)
	page.evaluate.assert_not_awaited()
	page.context.clear_cookies.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('profile', [{'id': 99}, None])
async def test_browser_login_verifies_popup_and_exports_only_site_cookies(monkeypatch, profile):
	monkeypatch.delenv('PROVIDERS', raising=False)
	provider = AppConfig.load_from_env().providers['agentrouter']
	page = make_page('https://agentrouter.org/login')
	page.goto = AsyncMock()
	popup = make_page('https://agentrouter.org/console')
	context = MagicMock()
	context.new_page = AsyncMock(return_value=page)
	context.close = AsyncMock()
	context.cookies = AsyncMock(return_value=[{'name': 'session', 'value': 'provider-session'}])
	monkeypatch.setattr(checkin, 'launch_login_context', AsyncMock(return_value=context))
	monkeypatch.setattr(checkin, 'prepare_browser_page', AsyncMock())
	navigate = AsyncMock()
	monkeypatch.setattr(checkin, 'navigate_login_page', navigate)
	clear_state = AsyncMock()
	monkeypatch.setattr(checkin, 'clear_site_login_state', clear_state)
	monkeypatch.setattr(checkin, 'save_login_screenshot', AsyncMock())
	oauth = AsyncMock(return_value=popup)
	monkeypatch.setattr(checkin, 'login_with_github_oauth', oauth)
	pipeline = Mock()
	pipeline.attach_mock(clear_state, 'clear')
	pipeline.attach_mock(navigate, 'navigate')
	pipeline.attach_mock(oauth, 'oauth')
	verify = AsyncMock(return_value=profile)
	monkeypatch.setattr(checkin, 'verify_browser_login', verify)

	result = await checkin.login_with_credentials('Test', provider, 'agentrouter', auth_method='github')

	assert verify.await_args is not None
	assert verify.await_args.args[0] is popup
	clear_state.assert_awaited_once()
	assert [call[0] for call in pipeline.mock_calls] == ['clear', 'navigate', 'oauth']
	page.goto.assert_awaited_once()
	context.cookies.assert_awaited_once_with(['https://agentrouter.org'])
	context.close.assert_awaited_once()
	if profile:
		assert result == BrowserLoginResult(cookies={'session': 'provider-session'}, api_user='99')
	else:
		assert result is None


@pytest.mark.asyncio
@pytest.mark.parametrize('login_success', [True, False])
async def test_agentrouter_dispatches_to_github_and_requires_verified_login(monkeypatch, login_success):
	monkeypatch.delenv('PROVIDERS', raising=False)
	account = AccountConfig(
		cookies=None,
		provider='agentrouter',
		github_username='test-user',
		github_password='test-password',
	)
	login = AsyncMock(
		return_value=BrowserLoginResult(cookies={'session': 'new'}, api_user='99') if login_success else None
	)
	requests = Mock(return_value=(True, None, {'success': True}))
	monkeypatch.setattr(checkin, 'login_with_credentials', login)
	monkeypatch.setattr(checkin, 'run_check_in_requests', requests)

	success, _, _ = await checkin.check_in_account(account, 0, AppConfig.load_from_env())

	assert success is login_success
	assert login.await_args is not None
	assert login.await_args.kwargs == {'auth_method': 'github'}
	assert login.await_args.args[3:] == ('test-user', 'test-password')
	if login_success:
		assert requests.call_args.args[0] == {'session': 'new'}
		assert requests.call_args.kwargs['api_user_override'] == '99'
	else:
		requests.assert_not_called()


@pytest.mark.parametrize('provider_name', ['anyrouter', 'agentrouter'])
def test_system_token_is_only_used_for_agentrouter_and_api_user_is_optional(monkeypatch, provider_name):
	monkeypatch.delenv('PROVIDERS', raising=False)
	provider = AppConfig.load_from_env().providers[provider_name]
	cookies = {'session': 'site'}
	account = AccountConfig(cookies=cookies, provider=provider_name, access_token=' system-token ')
	monkeypatch.setattr(checkin.httpx, 'Client', MagicMock())
	user_info = Mock(return_value={'success': True, 'quota': 10, 'display': 'Balance: 10'})
	monkeypatch.setattr(checkin, 'get_user_info', user_info)
	monkeypatch.setattr(checkin, 'execute_check_in', Mock(return_value=True))

	success, _, _ = checkin.run_check_in_requests(cookies, account, 'Test', provider)

	assert success is True
	headers = user_info.call_args.args[1]
	assert 'new-api-user' not in headers
	if provider_name == 'agentrouter':
		assert headers['Authorization'] == 'Bearer system-token'
	else:
		assert 'Authorization' not in headers
