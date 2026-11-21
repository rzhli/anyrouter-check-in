"""在本地浏览器完成 GitHub 2FA，再将会话直接上传到 Environment Secret。"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.browser import launch_login_context, load_browser_login_settings
from utils.firefox_session import capture_firefox_github_session
from utils.github_session import (
	GITHUB_SESSION_ENV,
	GitHubSessionError,
	encode_github_sessions,
	export_github_cookies,
)

if TYPE_CHECKING:
	from playwright._impl._api_structures import SetCookieParam


async def capture_github_session(
	account_name: str, *, use_proxy: bool = False, browser: str = 'cloakbrowser'
) -> list[SetCookieParam]:
	if browser == 'firefox':
		return capture_firefox_github_session(account_name, use_proxy=use_proxy)
	settings = replace(
		load_browser_login_settings(account_name, 'agentrouter', persist_profile=False),
		headless=False,
		persist_profile=False,
	)
	try:
		context = await launch_login_context(settings, use_proxy=use_proxy)
	except Exception:
		raise GitHubSessionError(
			'Cannot start CloakBrowser; install it and use a desktop session, '
			'or use system Firefox with uv run --extra firefox and --browser firefox'
		) from None
	try:
		page = await context.new_page()
		await page.goto('https://github.com/login', wait_until='domcontentloaded', timeout=60_000)
		print(f'请在浏览器中为账号「{account_name}」完成 GitHub 登录和六位验证码验证。')
		await asyncio.to_thread(input, '登录完成后，回到此终端按 Enter 验证并导出（Ctrl+C 取消）：')
		response = await page.goto('https://github.com/settings/profile', wait_until='domcontentloaded', timeout=60_000)
		current = urlparse(page.url)
		if (
			current.scheme != 'https'
			or current.hostname != 'github.com'
			or current.path != '/settings/profile'
			or response is None
			or response.status != 200
		):
			raise GitHubSessionError('GitHub login is not verified; finish login and 2FA before exporting')
		user = await page.locator('meta[name="user-login"]').get_attribute('content')
		if not user:
			raise GitHubSessionError('GitHub did not confirm an authenticated user; no session was exported')
		cookies = export_github_cookies(await context.cookies(['https://github.com/']))
		print(f'账号「{account_name}」的 GitHub 登录已验证。')
		return cookies
	except GitHubSessionError:
		raise
	except Exception:
		raise GitHubSessionError(
			'Local GitHub verification failed; check the browser or network and try again'
		) from None
	finally:
		await context.close()


def _resolve_secret_cli(repo: str, environment: str) -> str:
	if GITHUB_SESSION_ENV.upper().startswith('GITHUB_') or not re.fullmatch(
		r'[A-Za-z_][A-Za-z0-9_]*', GITHUB_SESSION_ENV
	):
		raise GitHubSessionError(
			'Invalid session Secret name; use GH_SESSION_STATE without the reserved GITHUB_ prefix'
		)
	if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
		raise GitHubSessionError('--repo must use owner/repository format')
	if not environment.strip() or environment.startswith('-'):
		raise GitHubSessionError('Invalid GitHub environment name')
	gh_path = shutil.which('gh')
	if not gh_path:
		raise GitHubSessionError('GitHub CLI is not installed; install gh and run gh auth login before uploading')
	return gh_path


def _classify_secret_failure(stderr: str) -> tuple[str, bool]:
	message = stderr.lower()
	if 'secret names' in message and 'github_' in message:
		return 'GitHub reserves the GITHUB_ prefix; update the exporter and workflow to use GH_SESSION_STATE', False
	if 'http 401' in message or 'bad credentials' in message or 'gh auth login' in message:
		return (
			'GitHub CLI authentication failed; run gh auth login --hostname github.com and check gh auth status',
			False,
		)
	if 'http 429' in message or 'rate limit' in message:
		return 'GitHub API rate limit reached; wait before retrying', False
	if 'http 403' in message or 'resource not accessible' in message or 'forbidden' in message:
		return (
			'GitHub denied access (HTTP 403); check repository admin access and token permissions for environment secrets',
			False,
		)
	if 'http 404' in message or 'not found' in message:
		return 'GitHub could not access the target (HTTP 404); check --repo, --environment and token access', False
	if 'http 422' in message:
		return 'GitHub rejected the secret (HTTP 422); check the secret size and environment restrictions', False
	if re.search(r'http 5\d\d', message):
		return 'GitHub API is temporarily unavailable; retry later', True
	if any(
		fragment in message
		for fragment in (
			'timeout',
			'connection',
			'error connecting',
			'tls',
			'resolve host',
			'no such host',
			'unexpected eof',
			'dial tcp',
			'broken pipe',
			'temporary failure in name resolution',
		)
	):
		return 'GitHub CLI network request failed; check terminal network/proxy settings and retry', True
	return 'Check gh auth status, repository access and environment name; alternatively export with --output', False


def _run_secret_command(command: list[str], *, payload: str | None = None, action: str) -> None:
	for attempt in range(3):
		try:
			result = subprocess.run(
				command,
				input=payload,
				text=True,
				capture_output=True,
				timeout=60,
				check=False,
			)
		except subprocess.TimeoutExpired:
			failure = f'{action} timed out; check GitHub CLI network access and retry'
			retryable = True
		except OSError:
			raise GitHubSessionError(f'{action} failed; check the gh installation and executable permissions') from None
		else:
			if result.returncode == 0:
				return
			hint, retryable = _classify_secret_failure(result.stderr or '')
			failure = f'{action} failed: {hint}'
		if not retryable or attempt == 2:
			raise GitHubSessionError(failure) from None
		print(f'{action}: temporary request failure; retrying ({attempt + 2}/3)...')
		time.sleep(attempt + 1)


def check_secret_upload_access(repo: str, environment: str) -> None:
	gh_path = _resolve_secret_cli(repo, environment)
	endpoint = f'repos/{repo}/environments/{quote(environment, safe="")}/secrets/public-key'
	_run_secret_command([gh_path, 'api', endpoint, '--silent'], action='Secret access check')


def upload_session_secret(payload: str, repo: str, environment: str) -> None:
	gh_path = _resolve_secret_cli(repo, environment)
	_run_secret_command(
		[gh_path, 'secret', 'set', GITHUB_SESSION_ENV, '--repo', repo, '--env', environment],
		payload=payload,
		action='Secret upload',
	)


def write_private_session_file(path: Path, payload: str) -> None:
	path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
	try:
		descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
		with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
			handle.write(payload)
	except FileExistsError:
		raise GitHubSessionError(
			'Session output already exists; choose a new path to avoid overwriting credentials'
		) from None


async def run(args: argparse.Namespace) -> None:
	if os.getenv('GITHUB_ACTIONS', '').lower() == 'true':
		raise GitHubSessionError('Session export must run on your local desktop, not in Actions')
	if args.repo:
		check_secret_upload_access(args.repo, args.environment)
	accounts = {}
	for name in args.account_name:
		if not name.strip() or name in accounts:
			raise GitHubSessionError('Account names must be nonempty and unique')
		accounts[name] = await capture_github_session(name, use_proxy=args.use_proxy, browser=args.browser)
	payload = encode_github_sessions(accounts)
	if args.repo:
		upload_session_secret(payload, args.repo, args.environment)
		print(f'已更新 {args.repo} / {args.environment} 的 {GITHUB_SESSION_ENV} Secret；未写入会话文件。')
	else:
		write_private_session_file(args.output, payload)
		print('已保存权限为 0600 的会话文件；它等同登录凭证，请勿分享或提交到仓库。')


def main() -> int:
	parser = argparse.ArgumentParser(description='手动完成 GitHub 2FA 并导出会话，不保存密码、验证码或浏览器 profile')
	parser.add_argument(
		'--account-name', action='append', required=True, help='与 ANYROUTER_ACCOUNTS.name 一致，可重复'
	)
	destination = parser.add_mutually_exclusive_group(required=True)
	destination.add_argument('--repo', help='直接通过 gh 上传 Secret，格式 owner/repository')
	destination.add_argument('--output', type=Path, help='可选：保存私密文件，建议放在忽略的 .session_exports/ 目录')
	parser.add_argument('--environment', default='production', help='Secret 所属 Environment，默认 production')
	parser.add_argument(
		'--browser',
		choices=('cloakbrowser', 'firefox'),
		default='cloakbrowser',
		help='本地登录浏览器；firefox 使用本机 Firefox，需 uv run --extra firefox',
	)
	parser.add_argument('--use-proxy', action='store_true', help='使用 CHECKIN_PROXY_URL 指定的本地代理')
	args = parser.parse_args()
	try:
		asyncio.run(run(args))
	except KeyboardInterrupt:
		print('已取消会话导出。', file=sys.stderr)
		return 1
	except GitHubSessionError as error:
		print(str(error), file=sys.stderr)
		return 1
	except Exception:
		print('会话导出失败；未输出敏感详情，请检查浏览器、目标文件权限及 gh 配置。', file=sys.stderr)
		return 1
	return 0


if __name__ == '__main__':
	sys.exit(main())
