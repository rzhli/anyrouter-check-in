import json

import pytest

from utils.config import AccountConfig, AppConfig, ProviderConfig, load_accounts_config


def test_builtin_provider_profile_persistence_defaults(monkeypatch):
	monkeypatch.delenv('PROVIDERS', raising=False)

	config = AppConfig.load_from_env()

	assert set(config.providers) == {'anyrouter', 'agentrouter'}
	assert config.providers['anyrouter'].persist_profile is True
	assert config.providers['agentrouter'].persist_profile is True
	assert config.providers['agentrouter'].github_oauth is True


def test_provider_profile_persistence_can_override_builtin(monkeypatch):
	monkeypatch.setenv(
		'PROVIDERS',
		json.dumps(
			{
				'anyrouter': {'domain': 'https://anyrouter.top', 'persist_profile': False},
				'agentrouter': {'domain': 'https://agentrouter.org', 'persist_profile': True},
			}
		),
	)

	config = AppConfig.load_from_env()

	assert config.providers['anyrouter'].persist_profile is False
	assert config.providers['agentrouter'].persist_profile is True
	assert config.providers['agentrouter'].github_oauth is True


def test_unsupported_provider_is_ignored(monkeypatch):
	monkeypatch.setenv('PROVIDERS', json.dumps({'custom': {'domain': 'https://custom.example.com'}}))

	config = AppConfig.load_from_env()

	assert set(config.providers) == {'anyrouter', 'agentrouter'}


def test_builtin_provider_override_does_not_require_domain(monkeypatch):
	monkeypatch.setenv('PROVIDERS', json.dumps({'agentrouter': {'use_proxy': False}}))

	provider = AppConfig.load_from_env().providers['agentrouter']

	assert provider.domain == 'https://agentrouter.org'
	assert provider.use_proxy is False
	assert provider.github_oauth is True
	assert provider.persist_profile is True
	assert provider.sign_in_path is None


def test_anyrouter_cookies_do_not_require_api_user(monkeypatch):
	monkeypatch.setenv(
		'ANYROUTER_ACCOUNTS',
		json.dumps(
			[
				{
					'provider': 'anyrouter',
					'cookies': {
						'acw_sc__v2': 'sc',
						'acw_tc': 'tc',
						'cdn_sec_tc': 'sec',
						'session': 'session',
					},
				}
			]
		),
	)

	accounts = load_accounts_config()
	assert accounts is not None
	assert accounts[0].api_user is None


def test_unsupported_account_provider_is_ignored(monkeypatch):
	monkeypatch.setenv(
		'ANYROUTER_ACCOUNTS',
		json.dumps(
			[
				{'provider': 'gorouter', 'access_token': 'old-token'},
				{'provider': 'anyrouter', 'cookies': {'session': 'session'}},
			]
		),
	)

	accounts = load_accounts_config()
	assert accounts is not None
	assert len(accounts) == 1
	assert accounts[0].provider == 'anyrouter'


def test_provider_from_dict_inherits_profile_persistence_from_defaults():
	defaults = ProviderConfig(name='custom', domain='https://old.example.com', persist_profile=True)

	provider = ProviderConfig.from_dict(
		'custom',
		{'domain': 'https://new.example.com'},
		defaults=defaults,
	)

	assert provider.persist_profile is True


def test_github_credentials_are_parsed(monkeypatch):
	monkeypatch.setenv(
		'ANYROUTER_ACCOUNTS',
		json.dumps([{'provider': 'agentrouter', 'github_username': 'test-user', 'github_password': 'test-password'}]),
	)

	accounts = load_accounts_config()

	assert accounts is not None
	assert accounts[0].github_username == 'test-user'
	assert accounts[0].github_password == 'test-password'
	assert accounts[0].has_github_credentials() is True
	assert AccountConfig(cookies=None).has_github_credentials() is False


@pytest.mark.parametrize('provider', ['anyrouter', 'agentrouter'])
def test_whitespace_token_without_credentials_is_rejected(monkeypatch, provider):
	monkeypatch.setenv('ANYROUTER_ACCOUNTS', json.dumps([{'provider': provider, 'access_token': '  '}]))

	assert load_accounts_config() is None


@pytest.mark.parametrize(
	'credentials',
	[{'access_token': 'unused-token'}, {'github_username': 'test-user', 'github_password': 'test-password'}],
)
def test_anyrouter_rejects_unsupported_auth_methods(monkeypatch, credentials):
	monkeypatch.setenv('ANYROUTER_ACCOUNTS', json.dumps([{'provider': 'anyrouter', **credentials}]))

	assert load_accounts_config() is None
