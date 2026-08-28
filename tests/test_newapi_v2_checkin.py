"""newapi_v2 签到流程（Bearer 访问令牌 + Turnstile）的单元测试。"""

import json
import sys
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from checkin import (
	check_in_account_v2,
	execute_check_in_v2,
	get_user_info,
	is_already_checked_in,
	is_checked_in_today,
)
from utils.config import AccountConfig, AppConfig


class FakeResponse:
	def __init__(self, payload, status_code: int = 200, *, raw: str | None = None):
		self._payload = payload
		self.status_code = status_code
		self.text = raw if raw is not None else json.dumps(payload)

	def json(self):
		if self._payload is None:
			raise json.JSONDecodeError('no json', self.text, 0)
		return self._payload


class FakeClient:
	"""按 (method, path) 返回预置响应，并记录调用参数。"""

	def __init__(self, routes: dict):
		self.routes = routes
		self.calls: list[dict] = []

	def _handle(self, method: str, url: str, params=None, **kwargs):
		path = url.split('://', 1)[-1].split('/', 1)[-1]
		self.calls.append({'method': method, 'url': url, 'params': params})
		key = (method, '/' + path)
		if key not in self.routes:
			raise AssertionError(f'unexpected request: {key}')
		response = self.routes[key]
		return response.pop(0) if isinstance(response, list) else response

	def get(self, url, **kwargs):
		return self._handle('GET', url, **kwargs)

	def post(self, url, **kwargs):
		return self._handle('POST', url, **kwargs)


def user_self_response(quota: int, used_quota: int) -> FakeResponse:
	return FakeResponse({'success': True, 'data': {'id': 1, 'quota': quota, 'used_quota': used_quota}})


def checkin_status_response(checked_in_today: bool) -> FakeResponse:
	return FakeResponse({'success': True, 'data': {'enabled': True, 'stats': {'checked_in_today': checked_in_today}}})


@pytest.fixture
def provider():
	return AppConfig.load_from_env().providers['justwoker']


# ---------------------------------------------------------------------------
# provider 配置
# ---------------------------------------------------------------------------


def test_builtin_newapi_v2_providers(monkeypatch):
	monkeypatch.delenv('PROVIDERS', raising=False)
	config = AppConfig.load_from_env()

	for name, domain in (('gorouter', 'https://gorouter.app'), ('justwoker', 'https://api.justwoker.icu')):
		provider = config.providers[name]
		assert provider.flow == 'newapi_v2'
		assert provider.uses_access_token() is True
		assert provider.domain == domain
		assert provider.login_path == '/sign-in'
		assert provider.sign_in_path == '/api/user/checkin'
		assert provider.checkin_status_path == '/api/user/checkin'
		assert provider.needs_waf_cookies() is False


def test_legacy_providers_keep_session_flow(monkeypatch):
	monkeypatch.delenv('PROVIDERS', raising=False)
	config = AppConfig.load_from_env()

	for name in ('anyrouter', 'agentrouter'):
		provider = config.providers[name]
		assert provider.flow == 'legacy'
		assert provider.uses_access_token() is False


def test_custom_provider_can_opt_into_newapi_v2(monkeypatch):
	monkeypatch.setenv(
		'PROVIDERS',
		json.dumps(
			{
				'mysite': {
					'domain': 'https://my.example.com',
					'flow': 'newapi_v2',
					'login_path': '/sign-in',
					'sign_in_path': '/api/user/checkin',
					'checkin_status_path': '/api/user/checkin',
					'quota_per_unit': 1000000,
				}
			}
		),
	)
	provider = AppConfig.load_from_env().providers['mysite']

	assert provider.uses_access_token() is True
	assert provider.quota_per_unit == 1000000


def test_newapi_v2_overrides_inherit_builtin_defaults(monkeypatch):
	monkeypatch.setenv('PROVIDERS', json.dumps({'gorouter': {'domain': 'https://gorouter.app', 'use_proxy': True}}))
	provider = AppConfig.load_from_env().providers['gorouter']

	assert provider.use_proxy is True
	assert provider.flow == 'newapi_v2'
	assert provider.checkin_status_path == '/api/user/checkin'


# ---------------------------------------------------------------------------
# 账号配置
# ---------------------------------------------------------------------------


def test_account_access_token_parsed():
	account = AccountConfig.from_dict({'provider': 'gorouter', 'access_token': 'tok'}, 0)

	assert account.access_token == 'tok'
	assert account.has_access_token() is True
	assert account.has_login_credentials() is False


def test_account_without_access_token():
	account = AccountConfig.from_dict({'api_user': '123', 'cookies': {'session': 'x'}}, 0)

	assert account.has_access_token() is False


def test_access_token_only_account_is_valid(monkeypatch):
	from utils.config import load_accounts_config

	monkeypatch.setenv(
		'ANYROUTER_ACCOUNTS',
		json.dumps([{'provider': 'gorouter', 'access_token': 'tok', 'name': 'GoRouter'}]),
	)
	accounts = load_accounts_config()

	assert accounts is not None
	assert len(accounts) == 1
	assert accounts[0].has_access_token() is True


def test_account_without_any_credential_is_rejected(monkeypatch):
	from utils.config import load_accounts_config

	monkeypatch.setenv('ANYROUTER_ACCOUNTS', json.dumps([{'provider': 'gorouter', 'name': 'x'}]))

	assert load_accounts_config() is None


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
	'message',
	['今日已签到', '已经签到', '重复签到', 'Already checked in', 'already signed in'],
)
def test_is_already_checked_in_true(message):
	assert is_already_checked_in(message) is True


@pytest.mark.parametrize('message', ['', 'Turnstile token 为空', '签到功能未启用', None])
def test_is_already_checked_in_false(message):
	assert is_already_checked_in(message) is False


def test_get_user_info_respects_quota_per_unit():
	client = FakeClient({('GET', '/api/user/self'): user_self_response(5000000, 500000)})

	info = get_user_info(client, {}, 'https://x.example.com/api/user/self', 1000000)

	assert info['success'] is True
	assert info['quota'] == 5.0
	assert info['used_quota'] == 0.5


# ---------------------------------------------------------------------------
# 签到请求
# ---------------------------------------------------------------------------


def test_execute_check_in_v2_success(provider):
	client = FakeClient(
		{('POST', '/api/user/checkin'): FakeResponse({'success': True, 'data': {'quota_awarded': 12500000}})}
	)

	assert execute_check_in_v2(client, 'acct', provider, {}, 'ts-token') is True
	assert client.calls[0]['params'] == {'turnstile': 'ts-token'}


def test_execute_check_in_v2_already_checked_in_is_success(provider):
	client = FakeClient({('POST', '/api/user/checkin'): FakeResponse({'success': False, 'message': '今日已签到'})})

	assert execute_check_in_v2(client, 'acct', provider, {}, 'ts-token') is True


def test_execute_check_in_v2_missing_turnstile_fails(provider):
	client = FakeClient(
		{('POST', '/api/user/checkin'): FakeResponse({'success': False, 'message': 'Turnstile token 为空'})}
	)

	assert execute_check_in_v2(client, 'acct', provider, {}, None) is False
	assert client.calls[0]['params'] is None


def test_execute_check_in_v2_http_error(provider):
	client = FakeClient({('POST', '/api/user/checkin'): FakeResponse({'success': False}, status_code=401)})

	assert execute_check_in_v2(client, 'acct', provider, {}, 'ts') is False


def test_execute_check_in_v2_invalid_json(provider):
	client = FakeClient({('POST', '/api/user/checkin'): FakeResponse(None, raw='<html>error</html>')})

	assert execute_check_in_v2(client, 'acct', provider, {}, 'ts') is False


def test_is_checked_in_today_true(provider):
	client = FakeClient({('GET', '/api/user/checkin'): checkin_status_response(True)})

	assert is_checked_in_today(client, 'acct', provider, {}) is True
	assert 'month' in client.calls[0]['params']


def test_is_checked_in_today_false(provider):
	client = FakeClient({('GET', '/api/user/checkin'): checkin_status_response(False)})

	assert is_checked_in_today(client, 'acct', provider, {}) is False


def test_is_checked_in_today_unknown_on_error(provider):
	client = FakeClient({('GET', '/api/user/checkin'): FakeResponse({'success': False, 'message': 'nope'})})

	assert is_checked_in_today(client, 'acct', provider, {}) is None


def test_is_checked_in_today_returns_none_without_status_path(provider):
	import dataclasses

	provider = dataclasses.replace(provider, checkin_status_path=None)

	assert is_checked_in_today(FakeClient({}), 'acct', provider, {}) is None


# ---------------------------------------------------------------------------
# 完整流程
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_in_account_v2_requires_access_token(provider):
	account = AccountConfig.from_dict({'provider': 'justwoker'}, 0)

	success, before, after = await check_in_account_v2(account, 'acct', provider)

	assert success is False
	assert before is None and after is None


@pytest.mark.asyncio
async def test_check_in_account_v2_skips_browser_when_already_checked_in(monkeypatch, provider):
	import checkin

	client = FakeClient(
		{
			('GET', '/api/user/self'): user_self_response(5000000, 1000000),
			('GET', '/api/user/checkin'): checkin_status_response(True),
		}
	)
	monkeypatch.setattr(checkin.httpx, 'Client', lambda **kwargs: _ctx(client))

	async def fail_browser(*args, **kwargs):
		raise AssertionError('browser must not be launched when already checked in')

	monkeypatch.setattr(checkin, 'get_turnstile_token_with_browser', fail_browser)

	account = AccountConfig.from_dict({'provider': 'justwoker', 'access_token': 'tok'}, 0)
	success, before, after = await check_in_account_v2(account, 'acct', provider)

	assert success is True
	assert before is not None
	assert before['quota'] == 10.0
	assert after == before


@pytest.mark.asyncio
async def test_check_in_account_v2_full_flow(monkeypatch, provider):
	import checkin

	client = FakeClient(
		{
			('GET', '/api/user/self'): [
				user_self_response(5000000, 1000000),
				user_self_response(17500000, 1000000),
			],
			('GET', '/api/user/checkin'): checkin_status_response(False),
			('POST', '/api/user/checkin'): FakeResponse({'success': True, 'data': {'quota_awarded': 12500000}}),
		}
	)
	monkeypatch.setattr(checkin.httpx, 'Client', lambda **kwargs: _ctx(client))

	async def fake_browser(*args, **kwargs):
		return 'ts-token'

	monkeypatch.setattr(checkin, 'get_turnstile_token_with_browser', fake_browser)

	account = AccountConfig.from_dict({'provider': 'justwoker', 'access_token': 'tok'}, 0)
	success, before, after = await check_in_account_v2(account, 'acct', provider)

	assert success is True
	assert before is not None and after is not None
	assert before['quota'] == 10.0
	assert after['quota'] == 35.0

	post_call = next(c for c in client.calls if c['method'] == 'POST')
	assert post_call['params'] == {'turnstile': 'ts-token'}


@pytest.mark.asyncio
async def test_check_in_account_v2_bad_token_fails_fast(monkeypatch, provider):
	import checkin

	client = FakeClient({('GET', '/api/user/self'): FakeResponse({'success': False}, status_code=401)})
	monkeypatch.setattr(checkin.httpx, 'Client', lambda **kwargs: _ctx(client))

	async def fail_browser(*args, **kwargs):
		raise AssertionError('browser must not be launched when the token is invalid')

	monkeypatch.setattr(checkin, 'get_turnstile_token_with_browser', fail_browser)

	account = AccountConfig.from_dict({'provider': 'justwoker', 'access_token': 'bad'}, 0)
	success, before, after = await check_in_account_v2(account, 'acct', provider)

	assert success is False
	assert after is None


@pytest.mark.asyncio
async def test_check_in_account_v2_fails_without_turnstile_token(monkeypatch, provider):
	import checkin

	client = FakeClient(
		{
			('GET', '/api/user/self'): user_self_response(5000000, 1000000),
			('GET', '/api/user/checkin'): checkin_status_response(False),
		}
	)
	monkeypatch.setattr(checkin.httpx, 'Client', lambda **kwargs: _ctx(client))

	async def no_token(*args, **kwargs):
		return None

	monkeypatch.setattr(checkin, 'get_turnstile_token_with_browser', no_token)

	account = AccountConfig.from_dict({'provider': 'justwoker', 'access_token': 'tok'}, 0)
	success, _, _ = await check_in_account_v2(account, 'acct', provider)

	assert success is False


@pytest.mark.asyncio
async def test_check_in_account_dispatches_to_v2(monkeypatch):
	import checkin

	monkeypatch.delenv('PROVIDERS', raising=False)
	called = {}

	async def fake_v2(account, account_name, provider_config):
		called['provider'] = provider_config.name
		return True, None, None

	monkeypatch.setattr(checkin, 'check_in_account_v2', fake_v2)

	account = AccountConfig.from_dict({'provider': 'gorouter', 'access_token': 'tok'}, 0)
	success, _, _ = await checkin.check_in_account(account, 0, AppConfig.load_from_env())

	assert success is True
	assert called['provider'] == 'gorouter'


class _ctx:
	"""把 FakeClient 包装成上下文管理器。"""

	def __init__(self, client):
		self.client = client

	def __enter__(self):
		return self.client

	def __exit__(self, *exc_info):
		return False
