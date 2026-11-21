"""GitHub 会话的最小化导出与校验。"""

from __future__ import annotations

import json
import math
import os
import time
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
	from playwright._impl._api_structures import SetCookieParam

GITHUB_SESSION_ENV = 'GITHUB_SESSION_STATE'
MAX_SESSION_BYTES = 48 * 1024
COOKIE_FIELDS = ('name', 'value', 'domain', 'path', 'expires', 'httpOnly', 'secure', 'sameSite')


class GitHubSessionError(ValueError):
	"""会话错误信息不包含敏感值。"""


def _validate_cookies(cookies: object) -> list[SetCookieParam]:
	if not isinstance(cookies, list) or not cookies or len(cookies) > 256:
		raise GitHubSessionError('GitHub session must contain between 1 and 256 cookies')
	validated: list[SetCookieParam] = []
	identities = set()
	for cookie in cookies:
		if not isinstance(cookie, dict) or any(field not in cookie for field in COOKIE_FIELDS):
			raise GitHubSessionError('GitHub session contains an invalid cookie record')
		if cookie['domain'] not in ('github.com', '.github.com'):
			raise GitHubSessionError('GitHub session may only contain github.com cookies')
		if cookie['secure'] is not True or not isinstance(cookie['httpOnly'], bool):
			raise GitHubSessionError('GitHub session cookies must use HTTPS')
		if cookie['sameSite'] not in ('Strict', 'Lax', 'None'):
			raise GitHubSessionError('GitHub session contains an invalid SameSite setting')
		for field in ('name', 'value', 'path'):
			value = cookie[field]
			if not isinstance(value, str) or not value or len(value) > 8192 or any(ord(char) < 32 for char in value):
				raise GitHubSessionError('GitHub session contains an invalid cookie field')
		if not cookie['path'].startswith('/'):
			raise GitHubSessionError('GitHub session contains an invalid cookie path')
		expires = cookie['expires']
		if isinstance(expires, bool) or not isinstance(expires, (int, float)) or not math.isfinite(expires):
			raise GitHubSessionError('GitHub session contains an invalid expiration time')
		if expires != -1 and expires <= time.time():
			continue
		identity = (cookie['name'], cookie['domain'], cookie['path'])
		if identity in identities:
			raise GitHubSessionError('GitHub session contains duplicate cookies')
		identities.add(identity)
		validated.append(cast('SetCookieParam', {field: cookie[field] for field in COOKIE_FIELDS}))
	if not any(cookie['name'] == 'user_session' and cookie['path'] == '/' for cookie in validated):
		raise GitHubSessionError(
			'GitHub session is missing or expired; complete local GitHub login and export it again'
		)
	return validated


def export_github_cookies(cookies: Sequence[Mapping[str, object]]) -> list[SetCookieParam]:
	"""排除其他站点 Cookie 和额外元数据。"""
	return _validate_cookies([cookie for cookie in cookies if cookie.get('domain') in ('github.com', '.github.com')])


def _unique_object(pairs: list[tuple]) -> dict:
	result = {}
	for key, value in pairs:
		if key in result:
			raise GitHubSessionError('GitHub session contains duplicate JSON fields')
		result[key] = value
	return result


def parse_github_sessions(raw: str) -> dict[str, list[SetCookieParam]]:
	if len(raw.encode('utf-8')) > MAX_SESSION_BYTES:
		raise GitHubSessionError('GitHub session exceeds the GitHub Secret size limit')
	try:
		payload = json.loads(raw, object_pairs_hook=_unique_object)
	except (json.JSONDecodeError, RecursionError):
		raise GitHubSessionError('GITHUB_SESSION_STATE must contain valid session JSON') from None
	if not isinstance(payload, dict) or type(payload.get('version')) is not int or payload['version'] != 1:
		raise GitHubSessionError('Unsupported GitHub session format; export it again using scripts/github_session.py')
	accounts = payload.get('accounts')
	if not isinstance(accounts, dict) or not accounts or len(accounts) > 16:
		raise GitHubSessionError('GitHub session must contain between 1 and 16 named accounts')
	result = {}
	for name, session in accounts.items():
		if not isinstance(name, str) or not name.strip() or len(name) > 256 or not isinstance(session, dict):
			raise GitHubSessionError('GitHub session contains an invalid account entry')
		result[name] = _validate_cookies(session.get('cookies'))
	return result


def encode_github_sessions(accounts: Mapping[str, Sequence[Mapping[str, object]]]) -> str:
	payload = {'version': 1, 'accounts': {name: {'cookies': cookies} for name, cookies in accounts.items()}}
	raw = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
	parse_github_sessions(raw)
	return raw


def load_github_session(account_name: str) -> list[SetCookieParam] | None:
	raw = os.getenv(GITHUB_SESSION_ENV, '')
	if not raw.strip():
		return None
	accounts = parse_github_sessions(raw)
	if account_name not in accounts:
		raise GitHubSessionError(
			'No GitHub session matches this account name; export with --account-name matching ANYROUTER_ACCOUNTS.name'
		)
	return accounts[account_name]


def mask_github_cookie_values(cookies: Sequence[Mapping[str, object]]) -> None:
	"""Actions 中为每个 Cookie 单独注册日志遮罩。"""
	if os.getenv('GITHUB_ACTIONS', '').lower() != 'true':
		return
	for cookie in cookies:
		value = str(cookie['value']).replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A')
		print(f'::add-mask::{value}')
