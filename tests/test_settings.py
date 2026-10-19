"""The settings (settings.py): the approved names, the token kept secret."""

from __future__ import annotations

from oanda.settings import (
    ACCOUNT_ID,
    DECLARED,
    ENVIRONMENT,
    LIVE,
    PAPER,
    PRACTICE,
    SYNTHETIC,
    TOKEN,
    config_from,
)


def test_the_approved_names_and_the_token_secret() -> None:
    by_name = {setting.name: setting for setting in DECLARED}
    assert {TOKEN, ACCOUNT_ID, ENVIRONMENT} <= set(by_name)
    assert by_name[TOKEN].secret and not by_name[ACCOUNT_ID].secret


def test_the_environment_is_the_role_common_setting_practice_is_paper() -> None:
    """Contract v26, G1 question 4: `live`, and `paper` labelled in OANDA's
    word, Practice, first among the settings and Practice until a person
    chooses Live; it serves every role."""
    first = DECLARED[0]
    assert first.name == ENVIRONMENT and first.required and first.default == PAPER
    assert [(c.value, c.label) for c in first.choices] == [(LIVE, "Live"), (PAPER, "Practice")]
    assert set(first.roles) == {"custody", "connectivity", "data"}
    config = config_from({ENVIRONMENT: PAPER, TOKEN: "t0ken-value"})
    assert config.environment == PRACTICE and config.environment_setting == PAPER
    assert config.credentials is not None and config.credentials.environment == PRACTICE


def test_a_value_saved_before_0_5_0_still_reaches_practice() -> None:
    """`practice`, saved before the setting was the role-common one: the
    practice host still, and kept as saved for the guard's badge."""
    config = config_from({ENVIRONMENT: "practice"})
    assert config.environment == PRACTICE and config.environment_setting == "practice"


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
