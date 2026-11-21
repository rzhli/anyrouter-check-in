"""使用本机 Firefox 的临时会话完成人工 GitHub 登录。"""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from utils.github_session import GitHubSessionError, export_github_cookies
from utils.proxy import get_proxy_server

if TYPE_CHECKING:
	from playwright._impl._api_structures import SetCookieParam


def _configure_proxy(options: Any, *, use_proxy: bool) -> None:
	options.set_preference('network.proxy.type', 0)
	if not use_proxy:
		return
	server = get_proxy_server()
	if not server:
		raise GitHubSessionError('--use-proxy requires CHECKIN_PROXY_URL to be set')
	try:
		proxy = urlparse(server)
		port = proxy.port
		if (
			proxy.scheme not in ('http', 'socks5', 'socks5h')
			or not proxy.hostname
			or not port
			or proxy.username is not None
			or proxy.password is not None
		):
			raise ValueError
	except ValueError:
		raise GitHubSessionError(
			'Firefox requires an HTTP or SOCKS5 proxy with an explicit port and no embedded credentials'
		) from None
	options.set_preference('network.proxy.type', 1)
	options.set_preference('network.proxy.no_proxies_on', '')
	if proxy.scheme == 'http':
		for protocol in ('http', 'ssl'):
			options.set_preference(f'network.proxy.{protocol}', proxy.hostname)
			options.set_preference(f'network.proxy.{protocol}_port', port)
	else:
		options.set_preference('network.proxy.socks', proxy.hostname)
		options.set_preference('network.proxy.socks_port', port)
		options.set_preference('network.proxy.socks_version', 5)
		options.set_preference('network.proxy.socks_remote_dns', True)


def _launch_firefox(*, use_proxy: bool) -> Any:
	try:
		from selenium import webdriver
		from selenium.webdriver.firefox.options import Options
	except ImportError:
		raise GitHubSessionError(
			'Firefox support requires Selenium; run uv run --extra firefox python scripts/github_session.py --browser firefox ...'
		) from None
	binary = shutil.which('firefox')
	if not binary:
		raise GitHubSessionError('Firefox is not installed or not on PATH; install Firefox before exporting')
	options = Options()
	options.binary_location = binary
	options.page_load_strategy = 'eager'
	_configure_proxy(options, use_proxy=use_proxy)
	try:
		return webdriver.Firefox(options=options)
	except Exception:
		raise GitHubSessionError(
			'Cannot start Firefox; use a desktop session and install geckodriver on PATH, '
			'or allow Selenium Manager to download it'
		) from None


def capture_firefox_github_session(account_name: str, *, use_proxy: bool = False) -> list[SetCookieParam]:
	driver = _launch_firefox(use_proxy=use_proxy)
	try:
		driver.set_page_load_timeout(60)
		driver.get('https://github.com/login')
		print(f'请在 Firefox 中为账号「{account_name}」完成 GitHub 登录和六位验证码验证。')
		input('登录完成后，回到此终端按 Enter 验证并导出（Ctrl+C 取消）：')
		driver.get('https://github.com/settings/profile')
		current = urlparse(driver.current_url)
		if current.scheme != 'https' or current.hostname != 'github.com' or current.path != '/settings/profile':
			raise GitHubSessionError('GitHub login is not verified; finish login and 2FA before exporting')
		users = driver.find_elements('css selector', 'meta[name="user-login"]')
		if not users or not users[0].get_attribute('content'):
			raise GitHubSessionError('GitHub did not confirm an authenticated user; no session was exported')
		cookies = export_github_cookies(
			[
				{
					'name': cookie.get('name'),
					'value': cookie.get('value'),
					'domain': cookie.get('domain'),
					'path': cookie.get('path'),
					'expires': cookie.get('expiry', -1),
					'httpOnly': cookie.get('httpOnly', False),
					'secure': cookie.get('secure', False),
					'sameSite': cookie.get('sameSite', 'None'),
				}
				for cookie in driver.get_cookies()
			]
		)
		print(f'账号「{account_name}」的 GitHub 登录已验证。')
		return cookies
	except GitHubSessionError:
		raise
	except Exception:
		raise GitHubSessionError(
			'Local GitHub verification failed; check Firefox or network access and try again'
		) from None
	finally:
		try:
			driver.quit()
		except Exception:
			raise GitHubSessionError('Firefox cleanup failed; close its temporary window and try again') from None
