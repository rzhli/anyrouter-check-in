"""基于 TokenFuture 前端协议验证 Sub2API 签到，所有请求均使用 MockTransport。"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

import checkin
from utils import sub2api
from utils.config import AccountConfig, AppConfig, load_accounts_config


@pytest.fixture
def provider(monkeypatch):
	monkeypatch.delenv('PROVIDERS', raising=False)
	return AppConfig.load_from_env().providers['tokenfuture']


@pytest.fixture
def account():
	return AccountConfig(cookies=None, provider='tokenfuture', name='TokenFuture', access_token=' login-jwt ')


def mock_api(monkeypatch, provider, *, status=None, claim=None, rejection=None, after_failure=False):
	requests = []
	claimed = False

	def handler(request):
		nonlocal claimed
		requests.append(request)
		assert request.headers['Authorization'] == 'Bearer login-jwt'
		assert request.headers['X-User-UI-Request'] == '1'
		assert request.headers['Referer'] == 'https://tokenfuture.si/custom/public-benefits'
		if rejection:
			return rejection(request)
		path = request.url.path
		if path == provider.user_info_path:
			if claimed and after_failure:
				return httpx.Response(503)
			data = {'balance': '14.50' if claimed else '12.00'}
		elif path == provider.usage_info_path:
			data = {'total_actual_cost': 4.5 if claimed else 4.0, 'total_cost': 99.0}
		elif path == provider.checkin_status_path and request.method == 'GET':
			data = status if status is not None else {'enabled': True, 'checked_in': False, 'server_date': '2026-11-23'}
		elif path == provider.sign_in_path and request.method == 'POST':
			assert json.loads(request.content) == {}
			assert not request.url.query
			claimed = True
			data = claim if claim is not None else {'already_checked_in': False, 'record': {'amount': '3.00'}}
		else:
			raise AssertionError(f'Unexpected request: {request.method} {path}')
		return httpx.Response(200, json={'code': 0, 'data': data})

	client_type = httpx.AsyncClient
	monkeypatch.setattr(
		sub2api.httpx,
		'AsyncClient',
		lambda **kwargs: client_type(transport=httpx.MockTransport(handler), **kwargs),
	)
	monkeypatch.setattr(sub2api, 'get_proxy_server', lambda **kwargs: None)
	return requests


def test_tokenfuture_defaults_and_overrides(monkeypatch, provider):
	assert provider.domain == 'https://tokenfuture.si'
	assert provider.flow == 'sub2api'
	assert provider.uses_access_token()
	assert not provider.needs_waf_cookies()
	assert not provider.github_oauth
	assert provider.quota_per_unit == 1
	assert provider.token_storage_key == 'auth_token'
	monkeypatch.setenv('PROVIDERS', json.dumps({'tokenfuture': {'use_proxy': True}}))
	override = AppConfig.load_from_env().providers['tokenfuture']
	assert override.use_proxy
	assert override.flow == provider.flow
	assert override.sign_in_path == provider.sign_in_path
	assert override.usage_info_path == provider.usage_info_path


@pytest.mark.parametrize('credentials', [{'access_token': 'jwt'}, {'email': 'test@example.com', 'password': 'pass'}])
def test_tokenfuture_account_credentials_accepted(monkeypatch, credentials):
	monkeypatch.setenv('ANYROUTER_ACCOUNTS', json.dumps([{'provider': 'tokenfuture', **credentials}]))
	accounts = load_accounts_config()
	assert accounts is not None and len(accounts) == 1
	assert accounts[0].provider == 'tokenfuture'


@pytest.mark.parametrize('credentials', [{}, {'cookies': {'session': 'old'}}, {'email': 'test@example.com'}])
def test_tokenfuture_requires_supported_credentials(monkeypatch, credentials):
	monkeypatch.setenv('ANYROUTER_ACCOUNTS', json.dumps([{'provider': 'tokenfuture', **credentials}]))
	assert load_accounts_config() is None


@pytest.mark.parametrize('payload', [None, [], {}, {'success': True}, {'code': False, 'data': {}}, {'code': 0}])
def test_sub2api_does_not_accept_newapi_or_malformed_payloads(payload):
	with pytest.raises(sub2api.Sub2APIError):
		sub2api.parse_sub2api_response(payload)


async def test_claim_and_balance_notification(account, provider, monkeypatch):
	requests = mock_api(monkeypatch, provider)
	login = AsyncMock()
	monkeypatch.setattr(sub2api, 'login_sub2api_with_browser', login)
	success, before, after = await checkin.check_in_account(account, 0, AppConfig(providers={'tokenfuture': provider}))
	assert success
	assert before['quota'] == 12.0
	assert before['used_quota'] == 4.0
	assert after['quota'] == 14.5
	assert after['used_quota'] == 4.5
	assert after['quota'] + after['used_quota'] - before['quota'] - before['used_quota'] == 3.0
	assert sum(request.method == 'POST' for request in requests) == 1
	login.assert_not_awaited()


async def test_already_checked_in_skips_post(account, provider, monkeypatch):
	requests = mock_api(monkeypatch, provider, status={'enabled': True, 'checked_in': True})
	success, before, after = await sub2api.check_in_account_sub2api(account, 'TokenFuture', provider)
	assert success
	assert before == after
	assert all(request.method == 'GET' for request in requests)


@pytest.mark.parametrize(
	'status',
	[{'enabled': False, 'checked_in': False}, {'enabled': True}, {'enabled': True, 'checked_in': 'false'}],
)
async def test_closed_or_unknown_status_does_not_claim(account, provider, monkeypatch, status):
	requests = mock_api(monkeypatch, provider, status=status)
	success, before, after = await sub2api.check_in_account_sub2api(account, 'TokenFuture', provider)
	assert not success
	assert before['success']
	assert after is None
	assert all(request.method == 'GET' for request in requests)


async def test_concurrent_claim_already_checked_in_is_success(account, provider, monkeypatch):
	mock_api(monkeypatch, provider, claim={'already_checked_in': True, 'record': {'amount': '3.00'}})
	success, _, _ = await sub2api.check_in_account_sub2api(account, 'TokenFuture', provider)
	assert success


@pytest.mark.parametrize('claim', [{}, {'record': {}}, {'record': {'amount': 'NaN'}}, {'record': {'amount': True}}])
async def test_missing_or_invalid_reward_is_failure(account, provider, monkeypatch, claim):
	mock_api(monkeypatch, provider, claim=claim)
	success, _, _ = await sub2api.check_in_account_sub2api(account, 'TokenFuture', provider)
	assert not success


@pytest.mark.parametrize(
	'rejection',
	[
		lambda request: httpx.Response(401, json={'code': 'UNAUTHORIZED', 'message': 'invalid token'}),
		lambda request: httpx.Response(200, text='<html>verification</html>'),
		lambda request: httpx.Response(200, json={'code': 'UNAUTHORIZED', 'message': 'invalid token'}),
	],
)
async def test_rejected_auth_or_html_never_claims(account, provider, monkeypatch, rejection, capsys):
	requests = mock_api(monkeypatch, provider, rejection=rejection)
	success, _, _ = await sub2api.check_in_account_sub2api(account, 'TokenFuture', provider)
	assert not success
	assert all(request.method == 'GET' for request in requests)
	assert 'login-jwt' not in capsys.readouterr().out


async def test_success_remains_success_when_balance_refresh_fails(account, provider, monkeypatch):
	mock_api(monkeypatch, provider, after_failure=True)
	success, before, after = await sub2api.check_in_account_sub2api(account, 'TokenFuture', provider)
	assert success
	assert before['success']
	assert after is None


async def test_email_login_takes_priority(account, provider, monkeypatch):
	mock_api(monkeypatch, provider)
	account.email, account.password = 'test@example.com', 'password'
	login = AsyncMock(return_value=('login-jwt', {}))
	monkeypatch.setattr(sub2api, 'login_sub2api_with_browser', login)
	success, _, _ = await sub2api.check_in_account_sub2api(account, 'TokenFuture', provider)
	assert success
	login.assert_awaited_once_with(account, 'TokenFuture', provider)


async def test_failed_email_login_does_not_use_stale_token(account, provider, monkeypatch):
	account.email, account.password = 'test@example.com', 'password'
	login = AsyncMock(side_effect=sub2api.Sub2APIError('Login verification did not complete'))
	client = MagicMock()
	monkeypatch.setattr(sub2api, 'login_sub2api_with_browser', login)
	monkeypatch.setattr(sub2api.httpx, 'AsyncClient', client)
	assert await sub2api.check_in_account_sub2api(account, 'TokenFuture', provider) == (False, None, None)
	client.assert_not_called()


@pytest.mark.parametrize('verification_token', ['turnstile-token', None])
async def test_browser_login_waits_for_turnstile_and_closes_context(account, provider, monkeypatch, verification_token):
	account.email, account.password = 'test@example.com', 'password'
	response = SimpleNamespace(
		status=200, json=AsyncMock(return_value={'code': 0, 'data': {'access_token': 'login-jwt'}})
	)
	pending = AsyncMock()
	# expect_response.value 是可等待对象。
	future = asyncio.get_running_loop().create_future()
	future.set_result(response)
	pending.__aenter__.return_value.value = future
	page = SimpleNamespace(
		evaluate=AsyncMock(side_effect=[None, True]),
		expect_response=MagicMock(return_value=pending),
		locator=MagicMock(),
	)
	page.locator.return_value.first.click = AsyncMock()
	context = SimpleNamespace(
		new_page=AsyncMock(return_value=page), cookies=AsyncMock(return_value=[]), close=AsyncMock()
	)
	monkeypatch.setattr(
		sub2api, 'load_browser_login_settings', lambda *args, **kwargs: SimpleNamespace(wait_timeout_ms=5000)
	)
	monkeypatch.setattr(sub2api, 'launch_login_context', AsyncMock(return_value=context))
	monkeypatch.setattr(sub2api, 'prepare_browser_page', AsyncMock())
	monkeypatch.setattr(sub2api, 'navigate_and_wait', AsyncMock())
	fill = AsyncMock()
	verification = AsyncMock(return_value=verification_token)
	monkeypatch.setattr(sub2api, 'fill_email_credentials', fill)
	monkeypatch.setattr(sub2api, 'wait_for_turnstile_token', verification)
	if verification_token:
		assert await sub2api.login_sub2api_with_browser(account, 'TokenFuture', provider) == ('login-jwt', {})
	else:
		with pytest.raises(sub2api.Sub2APIError, match='verification did not complete'):
			await sub2api.login_sub2api_with_browser(account, 'TokenFuture', provider)
		page.locator.assert_not_called()
	fill.assert_awaited_once_with(page, account.email, account.password, 5000)
	verification.assert_awaited_once_with(page, 5000)
	context.close.assert_awaited_once()
