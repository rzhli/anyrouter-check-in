"""AnyRouter HTTP 200、无效会话与奖励未到账的回归测试。"""

from unittest.mock import AsyncMock

import httpx
import pytest

import checkin
from utils.browser import BrowserLoginResult
from utils.config import AccountConfig, AppConfig


@pytest.fixture
def provider(monkeypatch):
	monkeypatch.delenv('PROVIDERS', raising=False)
	return AppConfig.load_from_env().providers['anyrouter']


def mock_api(monkeypatch, *, before=None, after=None, claim=None, before_status=200, after_status=200):
	requests = []
	queried = 0

	def handler(request):
		nonlocal queried
		requests.append(request)
		if request.url.path == '/api/user/self' and request.method == 'GET':
			queried += 1
			payload = before if queried == 1 else after
			payload = payload if payload is not None else {'success': True, 'data': {'quota': 5000000, 'used_quota': 0}}
			status = before_status if queried == 1 else after_status
			return httpx.Response(status, json=payload)
		assert request.url.path == '/api/user/sign_in' and request.method == 'POST'
		return httpx.Response(200, json=claim if claim is not None else {'success': True})

	client_type = httpx.Client
	monkeypatch.setattr(
		checkin.httpx, 'Client', lambda **kwargs: client_type(transport=httpx.MockTransport(handler), **kwargs)
	)
	monkeypatch.setattr(checkin, 'get_proxy_server', lambda **kwargs: None)
	return requests


@pytest.mark.parametrize(
	'payload',
	[
		{'success': False, 'code': 0, 'message': '未登录'},
		{'success': False, 'ret': 1, 'message': '签到失败'},
		{'ret': 0, 'code': 0, 'message': '签到失败'},
		{'success': 'false'},
		{'code': False},
		{'ret': True},
		{},
		[],
	],
)
def test_failure_flags_and_unknown_response_are_never_success(provider, payload, capsys):
	with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))) as client:
		assert not checkin.execute_check_in(client, 'AnyRouter', provider, {})
	assert '[SUCCESS]' not in capsys.readouterr().out


@pytest.mark.parametrize('status', [401, 403])
def test_rejected_session_does_not_call_checkin(monkeypatch, provider, status, capsys):
	requests = mock_api(monkeypatch, before_status=status)
	account = AccountConfig(cookies={'session': 'expired'})
	success, before, after = checkin.run_check_in_requests(account.cookies, account, 'AnyRouter', provider)
	assert not success
	assert before['status_code'] == status
	assert after['success'] is False
	assert 'email+password' in after['error']
	assert len(requests) == 1
	assert requests[0].method == 'GET'
	assert '[SUCCESS]' not in capsys.readouterr().out


def test_api_acceptance_without_credited_reward_is_failure(monkeypatch, provider, capsys):
	mock_api(monkeypatch)
	account = AccountConfig(cookies={'session': 'valid'})
	success, before, after = checkin.run_check_in_requests(account.cookies, account, 'AnyRouter', provider)
	assert not success
	assert before['quota'] == after['quota']
	output = capsys.readouterr().out
	assert 'no reward was credited' in output
	assert '[SUCCESS]' not in output


def test_reward_is_verified_even_with_concurrent_usage(monkeypatch, provider, capsys):
	mock_api(
		monkeypatch,
		before={'success': True, 'data': {'quota': 5000000, 'used_quota': 1000000}},
		after={'success': True, 'data': {'quota': 16500000, 'used_quota': 2000000}},
	)
	account = AccountConfig(cookies={'session': 'valid'}, api_user='99')
	success, before, after = checkin.run_check_in_requests(account.cookies, account, 'AnyRouter', provider)
	assert success
	assert after['quota'] - before['quota'] == 23
	assert '+$25.00' in capsys.readouterr().out


def test_explicit_already_checked_in_with_valid_balance_is_success(monkeypatch, provider):
	mock_api(monkeypatch, claim={'success': False, 'message': '今日已签到'})
	account = AccountConfig(cookies={'session': 'valid'})
	success, before, after = checkin.run_check_in_requests(account.cookies, account, 'AnyRouter', provider)
	assert success
	assert before['quota'] == after['quota']


def test_unavailable_after_balance_is_not_verified_success(monkeypatch, provider, capsys):
	mock_api(monkeypatch, after_status=401)
	account = AccountConfig(cookies={'session': 'valid'})
	success, _, after = checkin.run_check_in_requests(account.cookies, account, 'AnyRouter', provider)
	assert not success
	assert after['status_code'] == 401
	assert '[SUCCESS]' not in capsys.readouterr().out


@pytest.mark.parametrize(
	'data',
	[None, {}, {'quota': 1}, {'quota': True, 'used_quota': 0}, {'quota': float('nan'), 'used_quota': 0}],
)
def test_missing_or_invalid_balance_is_not_silently_zero(data):
	assert checkin.parse_user_info_payload({'success': True, 'data': data})['success'] is False


async def test_email_login_refreshes_session_and_resolves_user_id(monkeypatch, provider):
	requests = mock_api(monkeypatch, after={'success': True, 'data': {'quota': 17500000, 'used_quota': 0}})
	account = AccountConfig(cookies={'session': 'stale'}, email='test@example.com', password='password')
	login = AsyncMock(return_value=BrowserLoginResult(cookies={'session': 'fresh'}, api_user='99'))
	prepare = AsyncMock()
	monkeypatch.setattr(checkin, 'login_with_credentials', login)
	monkeypatch.setattr(checkin, 'prepare_cookies', prepare)
	success, _, _ = await checkin.check_in_account(account, 0, AppConfig(providers={'anyrouter': provider}))
	assert success
	assert all(request.headers['new-api-user'] == '99' for request in requests)
	assert all('session=fresh' in request.headers['Cookie'] for request in requests)
	prepare.assert_not_awaited()
