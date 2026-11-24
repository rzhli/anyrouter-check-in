"""TokenFuture / Sub2API 签到：登录 JWT、签到状态与美元余额。"""

import asyncio
import math
from urllib.parse import urlsplit

import httpx

from utils.browser import (
	fill_email_credentials,
	launch_login_context,
	load_browser_login_settings,
	navigate_and_wait,
	prepare_browser_page,
	save_login_screenshot,
	wait_for_turnstile_token,
)
from utils.config import AccountConfig, ProviderConfig
from utils.proxy import get_proxy_server


class Sub2APIError(RuntimeError):
	"""接口未返回可验证的成功结果。"""


def parse_sub2api_response(payload) -> dict:
	"""Sub2API 的成功响应为 {code: 0, data: {...}}，并非 NewAPI 的 success。"""
	if not isinstance(payload, dict):
		raise Sub2APIError('Invalid JSON response')
	if type(payload.get('code')) is not int or payload['code'] != 0:
		raise Sub2APIError('API rejected request')
	data = payload.get('data')
	if not isinstance(data, dict):
		raise Sub2APIError('Missing response data')
	return data


def _dollars(value) -> float:
	if isinstance(value, bool) or not isinstance(value, (str, int, float)):
		raise Sub2APIError('Missing or invalid dollar amount')
	try:
		amount = float(value)
	except ValueError:
		raise Sub2APIError('Invalid dollar amount') from None
	if not math.isfinite(amount):
		raise Sub2APIError('Invalid dollar amount')
	return round(amount, 2)


async def _request_data(client: httpx.AsyncClient, method: str, path: str, **kwargs) -> dict:
	response = await client.request(method, path, **kwargs)
	if response.status_code != 200:
		raise Sub2APIError(f'API returned HTTP {response.status_code}')
	try:
		payload = response.json()
	except ValueError:
		raise Sub2APIError('Expected JSON; check site verification and login validity') from None
	return parse_sub2api_response(payload)


async def _get_user_info(client: httpx.AsyncClient, provider: ProviderConfig) -> dict:
	if not provider.usage_info_path:
		raise Sub2APIError('Missing usage_info_path')
	responses: tuple[dict | BaseException, dict | BaseException] = await asyncio.gather(
		_request_data(client, 'GET', provider.user_info_path),
		_request_data(client, 'GET', provider.usage_info_path),
		return_exceptions=True,
	)
	user, usage = responses
	if isinstance(user, BaseException):
		raise user
	if isinstance(usage, BaseException):
		raise usage
	quota = _dollars(user.get('balance'))
	used = _dollars(usage.get('total_actual_cost'))
	return {
		'success': True,
		'quota': quota,
		'used_quota': used,
		'display': f':money: Current balance: ${quota}, Used: ${used}',
	}


async def login_sub2api_with_browser(
	account: AccountConfig, account_name: str, provider: ProviderConfig
) -> tuple[str, dict] | None:
	"""由站点登录表单处理 Turnstile，取回登录 JWT；不使用 NewAPI 的 Cookie 验证。"""
	settings = load_browser_login_settings(account_name, provider.name, persist_profile=provider.persist_profile)
	context = await launch_login_context(settings, use_proxy=provider.use_proxy)
	try:
		page = await context.new_page()
		await prepare_browser_page(page)
		await navigate_and_wait(page, provider.domain, settings.wait_timeout_ms)
		# 邮箱登录每次获取新令牌，避免复用已经过期的 JWT。
		await page.evaluate(
			"""() => {
				for (const key of ['auth_token', 'refresh_token', 'auth_user', 'token_expires_at']) {
					localStorage.removeItem(key);
				}
			}"""
		)
		await navigate_and_wait(page, f'{provider.domain}{provider.login_path}', settings.wait_timeout_ms)
		await fill_email_credentials(page, account.email or '', account.password or '', settings.wait_timeout_ms)
		turnstile_enabled = await page.evaluate('() => window.__APP_CONFIG__?.turnstile_enabled === true')
		if turnstile_enabled and not await wait_for_turnstile_token(page, settings.wait_timeout_ms):
			await save_login_screenshot(page, provider.name, account_name, 'turnstile-timeout')
			proxy_hint = (
				'check the configured proxy exit'
				if provider.use_proxy
				else 'enable tokenfuture.use_proxy in PROVIDERS to use CHECKIN_PROXY_URL'
			)
			raise Sub2APIError(
				f'Login verification did not complete; in Actions, {proxy_hint}, or configure a valid login access_token'
			)
		async with page.expect_response(
			lambda response: urlsplit(response.url).path == '/api/v1/auth/login' and response.request.method == 'POST',
			timeout=settings.wait_timeout_ms,
		) as pending:
			await page.locator('button[type="submit"]').first.click(timeout=settings.wait_timeout_ms)
		response = await pending.value
		if response.status != 200:
			raise Sub2APIError(f'Login failed (HTTP {response.status})')
		data = parse_sub2api_response(await response.json())
		if data.get('requires_2fa'):
			raise Sub2APIError('Login requires two-factor verification; configure a valid access_token')
		token = data.get('access_token')
		if not isinstance(token, str) or not token.strip():
			raise Sub2APIError('Login did not return an access token')
		cookies = {item['name']: item['value'] for item in await context.cookies([provider.domain])}
		return token.strip(), cookies
	finally:
		await context.close()


async def check_in_account_sub2api(
	account: AccountConfig, account_name: str, provider: ProviderConfig
) -> tuple[bool, dict | None, dict | None]:
	"""查询服务端今日状态，未签到时 POST 空 JSON，并获取签到前后余额。"""
	before = None
	try:
		cookies: dict[str, str] = {}
		if account.has_login_credentials():
			print(f'[AUTH] {account_name}: Using email/password login via browser')
			login = await login_sub2api_with_browser(account, account_name, provider)
			if not login:
				raise Sub2APIError('Browser login failed')
			token, cookies = login
		elif account.has_access_token():
			print(f'[AUTH] {account_name}: Using login access token (Bearer)')
			token = (account.access_token or '').strip()
		else:
			raise Sub2APIError('TokenFuture requires access_token or email+password')

		client_kwargs: dict = {
			'base_url': provider.domain,
			'http2': True,
			'timeout': 30.0,
			'cookies': cookies,
			'headers': {
				'Authorization': f'Bearer {token}',
				'Accept': 'application/json',
				'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
				'X-User-UI-Request': '1',
				'Origin': provider.domain,
				'Referer': f'{provider.domain}{provider.checkin_page_path}',
			},
		}
		proxy = get_proxy_server(use_proxy=provider.use_proxy)
		if proxy:
			client_kwargs['proxy'] = proxy
		async with httpx.AsyncClient(**client_kwargs) as client:
			before = await _get_user_info(client, provider)
			print(before['display'])
			if not provider.checkin_status_path or not provider.sign_in_path:
				raise Sub2APIError('Missing check-in API paths')
			status = await _request_data(client, 'GET', provider.checkin_status_path)
			if status.get('checked_in') is True:
				print(f'[SUCCESS] {account_name}: Already checked in today')
				return True, before, before
			if status.get('enabled') is not True:
				raise Sub2APIError('Check-in is not open on the site')
			if status.get('checked_in') is not False:
				raise Sub2APIError('Check-in status is unavailable')
			claim = await _request_data(client, 'POST', provider.sign_in_path, json={})
			if claim.get('already_checked_in') is True:
				print(f'[SUCCESS] {account_name}: Already checked in today')
			else:
				record = claim.get('record')
				if not isinstance(record, dict):
					raise Sub2APIError('Check-in response is missing its reward record')
				print(f'[SUCCESS] {account_name}: Check-in successful! Received ${_dollars(record.get("amount"))}')
			try:
				after = await _get_user_info(client, provider)
				print(after['display'])
			except (Sub2APIError, httpx.HTTPError) as error:
				print(f'[WARN] {account_name}: Check-in completed but balance refresh failed ({type(error).__name__})')
				after = None
			return True, before, after
	except Sub2APIError as error:
		print(f'[FAILED] {account_name}: {error}')
	except Exception as error:
		# 不输出含 Authorization、验证码、登录响应或代理凭据的异常内容。
		print(f'[FAILED] {account_name}: Sub2API check-in failed ({type(error).__name__})')
	return False, before, None
