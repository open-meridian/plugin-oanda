"""The settings (settings.py): the approved names, the token kept secret."""

from __future__ import annotations

from oanda.settings import (
    ACCOUNT_ID,
    DECLARED,
    ENVIRONMENT,
    LIVE,
    PRACTICE,
    SYNTHETIC,
    TOKEN,
    config_from,
)


def test_the_approved_names_and_the_token_secret() -> None:
    by_name = {setting.name: setting for setting in DECLARED}
    assert {TOKEN, ACCOUNT_ID, ENVIRONMENT} <= set(by_name)
    assert by_name[TOKEN].secret and not by_name[ACCOUNT_ID].secret
    assert [choice.value for choice in by_name[ENVIRONMENT].choices] == [PRACTICE, LIVE]


def test_credentials_are_whole_or_named_missing() -> None:
    config = config_from({ACCOUNT_ID: "101-001-1-001"})
    assert config.credentials is None and config.missing == (TOKEN,)
    assert not config.ready
    config = config_from({TOKEN: "t0ken-value", ACCOUNT_ID: "101-001-1-001", ENVIRONMENT: LIVE})
    assert config.credentials is not None and config.credentials.environment == LIVE
    assert "t0ken-value" not in repr(config)
    assert config.secrets() == ("t0ken-value",)


def test_an_unknown_environment_is_practice() -> None:
    assert config_from({ENVIRONMENT: "staging"}).environment == PRACTICE


def test_sample_data_needs_no_token() -> None:
    assert config_from({SYNTHETIC: True}).ready


def test_the_account_id_is_optional_from_0_3_0() -> None:
    """The token alone is a whole credential; the account is chosen on
    Connections, or named by `account_id` where it is given."""
    by_name = {setting.name: setting for setting in DECLARED}
    assert not by_name[ACCOUNT_ID].required and by_name[TOKEN].required
    config = config_from({TOKEN: "t0ken-value"})
    assert config.credentials is not None and config.missing == () and config.account_id == ""
    assert config_from({TOKEN: "t0ken-value", ACCOUNT_ID: " 101-001-1-001 "}).account_id == (
        "101-001-1-001"
    )
