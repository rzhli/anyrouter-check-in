import dataclasses
import json
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

import checkin
from utils import browser
from utils.config import AppConfig


@pytest.fixture
def provider(monkeypatch):
	monkeypatch.delenv('PROVIDERS', raising=False)
	return AppConfig.load_from_env().providers['anyrouter']


@pytest.mark.asyncio
@pytest.mark.parametrize('use_proxy', [True, False])
async def test_fresh_waf_cookies_override_configured_values_without_replacing_session(monkeypatch, provider, use_proxy):
	provider = dataclasses.replace(provider, use_proxy=use_proxy)
	configured = {
		'session': 'account-session',
		'acw_tc': 'old-tc',
		'cdn_sec_tc': 'old-sec',
		'acw_sc__v2': 'old-sc',
	}
	fresh = {'acw_tc': 'fresh-tc', 'cdn_sec_tc': 'fresh-sec', 'acw_sc__v2': 'fresh-sc'}
	get_waf = AsyncMock(return_value=fresh)
	monkeypatch.setattr(checkin, 'get_waf_cookies_with_browser', get_waf)

	result = await checkin.prepare_cookies('Test', provider, configured)

	assert result == {'session': 'account-session', **fresh}
	assert configured['acw_tc'] == 'old-tc'
	assert get_waf.await_args is not None
	assert get_waf.await_args.kwargs == {'use_proxy': use_proxy}


@pytest.mark.asyncio
async def test_no_waf_provider_keeps_configured_cookies(monkeypatch, provider):
	provider = dataclasses.replace(provider, bypass_method=None)
	configured = {'session': 'account-session', 'acw_tc': 'configured-tc'}
	get_waf = AsyncMock()
	monkeypatch.setattr(checkin, 'get_waf_cookies_with_browser', get_waf)

	assert await checkin.prepare_cookies('Test', provider, configured) == configured
	get_waf.assert_not_awaited()


@pytest.mark.asyncio
async def test_waf_failure_does_not_fall_back_to_stale_configured_cookies(monkeypatch, provider):
	monkeypatch.setattr(checkin, 'get_waf_cookies_with_browser', AsyncMock(return_value=None))

	assert await checkin.prepare_cookies('Test', provider, {'session': 'account-session', 'acw_tc': 'old-tc'}) is None


def non_json_response(text: str, content_type: str = 'text/html') -> MagicMock:
	response = MagicMock(status_code=200, text=text, headers={'content-type': content_type})
	response.json.side_effect = json.JSONDecodeError('invalid JSON', text, 0)
	return response


def test_user_info_reports_html_response_without_logging_body():
	response = non_json_response('<html>Access Verification; private-session-value</html>')
	client = Mock()
	client.get.return_value = response

	result = checkin.get_user_info(client, {}, 'https://anyrouter.top/api/user/self')

	assert result['success'] is False
	assert 'HTML' in result['error']
	assert 'HTTP 200' in result['error']
	assert 'WAF/verification' in result['error']
	assert 'private-session-value' not in result['error']


@pytest.mark.parametrize(
	'text, content_type',
	[
		('<html><script>const success = true;</script></html>', 'text/html'),
		('unsuccessful', 'text/plain'),
		('unexpected-private-session-value', 'application/octet-stream'),
	],
)
def test_non_json_checkin_responses_are_not_mistaken_for_success(provider, capsys, text, content_type):
	client = Mock()
	client.post.return_value = non_json_response(text, content_type)

	assert checkin.execute_check_in(client, 'Test', provider, {}) is False
	output = capsys.readouterr().out
	assert 'Expected JSON' in output
	assert 'unexpected-private-session-value' not in output


def test_legacy_plain_success_response_is_still_supported(provider):
	client = Mock()
	client.post.return_value = non_json_response('success', 'text/plain')

	assert checkin.execute_check_in(client, 'Test', provider, {}) is True


@pytest.mark.asyncio
@pytest.mark.parametrize('headless', ['true', 'false'])
async def test_actions_reports_slider_challenge_even_with_virtual_display(monkeypatch, headless):
	monkeypatch.setenv('GITHUB_ACTIONS', 'true')
	monkeypatch.setenv('CHECKIN_HEADLESS', headless)
	page = MagicMock()
	page.evaluate = AsyncMock(return_value=True)

	with pytest.raises(browser.InteractiveVerificationRequiredError, match='PROXY_SUBSCRIPTION_URL'):
		await browser.raise_if_interactive_verification_required(page)


@pytest.mark.asyncio
async def test_local_headless_mode_reports_slider_challenge(monkeypatch):
	monkeypatch.delenv('GITHUB_ACTIONS', raising=False)
	monkeypatch.setenv('CHECKIN_HEADLESS', 'true')
	page = MagicMock()
	page.evaluate = AsyncMock(return_value=True)

	with pytest.raises(browser.InteractiveVerificationRequiredError, match='interactive slide verification'):
		await browser.raise_if_interactive_verification_required(page)


@pytest.mark.asyncio
async def test_local_headed_browser_allows_manual_slider_verification(monkeypatch):
	monkeypatch.delenv('GITHUB_ACTIONS', raising=False)
	monkeypatch.setenv('CHECKIN_HEADLESS', 'false')
	page = MagicMock()
	page.evaluate = AsyncMock(return_value=True)

	await browser.raise_if_interactive_verification_required(page)

	page.evaluate.assert_not_awaited()


@pytest.mark.asyncio
async def test_normal_page_does_not_report_slider_challenge(monkeypatch):
	monkeypatch.setenv('GITHUB_ACTIONS', 'true')
	page = MagicMock()
	page.evaluate = AsyncMock(return_value=False)

	await browser.raise_if_interactive_verification_required(page)


@pytest.mark.asyncio
async def test_site_ready_does_not_silently_ignore_slider_challenge(monkeypatch):
	monkeypatch.setenv('GITHUB_ACTIONS', 'true')
	page = MagicMock()
	page.wait_for_load_state = AsyncMock()
	page.wait_for_function = AsyncMock()
	page.evaluate = AsyncMock(return_value=True)

	with pytest.raises(browser.InteractiveVerificationRequiredError):
		await browser.wait_for_site_ready(page)

	page.wait_for_function.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('challenge_during_warmup', [True, False])
async def test_login_navigation_stops_retrying_known_slider_challenge(monkeypatch, challenge_during_warmup):
	monkeypatch.setenv('GITHUB_ACTIONS', 'true')
	page = MagicMock()
	page.goto = AsyncMock()
	page.wait_for_function = AsyncMock()
	page.evaluate = AsyncMock(side_effect=[True] if challenge_during_warmup else [False, True])
	monkeypatch.setattr(browser, '_settle_page', AsyncMock())
	monkeypatch.setattr(browser, 'dismiss_popups', AsyncMock(return_value=0))

	with pytest.raises(browser.InteractiveVerificationRequiredError):
		await browser.navigate_login_page(page, 'https://agentrouter.org/login', 120_000)

	assert page.goto.await_count == (1 if challenge_during_warmup else 2)
	page.wait_for_function.assert_not_awaited()
