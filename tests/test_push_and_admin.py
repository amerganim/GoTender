"""Web push payloads and admin access rules."""

from __future__ import annotations

import pytest

from tenderradar.alerts.push import MAX_BODY, MAX_TITLE
from tenderradar.config import settings


# ------------------------------------------------------------- push limits


def test_payload_caps_are_well_under_the_protocol_limit():
    """An encrypted push payload is capped around 4KB; a rejected send is a
    silently missed alert, so the caps stay far below it."""
    assert MAX_TITLE + MAX_BODY < 1000


def test_push_is_disabled_until_both_keys_are_present(monkeypatch):
    """Half-configured keys must not look enabled, or every send fails."""
    monkeypatch.setattr(settings, "vapid_public_key", "pub")
    monkeypatch.setattr(settings, "vapid_private_key", "")
    assert not settings.push_enabled

    monkeypatch.setattr(settings, "vapid_private_key", "priv")
    assert settings.push_enabled


# --------------------------------------------------------------- admin acl


@pytest.mark.parametrize(
    "configured,candidate,allowed",
    [
        ("a@x.com", "a@x.com", True),
        ("a@x.com", "A@X.COM", True),          # case must not matter
        (" a@x.com , b@y.com ", "b@y.com", True),  # whitespace tolerated
        ("a@x.com", "c@z.com", False),
        ("", "a@x.com", False),                 # unset means nobody, not everybody
    ],
)
def test_admin_email_matching(monkeypatch, configured, candidate, allowed):
    monkeypatch.setattr(settings, "admin_emails", configured)
    assert (candidate.strip().lower() in settings.admin_email_set) is allowed


def test_empty_admin_list_locks_everyone_out(monkeypatch):
    """The dangerous default would be an empty list meaning 'no restriction'."""
    monkeypatch.setattr(settings, "admin_emails", "")
    assert settings.admin_email_set == set()


# ------------------------------------------------------- subscription shape


@pytest.mark.parametrize(
    "subscription",
    [
        {},
        {"endpoint": "https://push.example/x"},                  # no keys
        {"endpoint": "https://push.example/x", "keys": {}},      # empty keys
        {"endpoint": "https://push.example/x", "keys": {"p256dh": "k"}},  # no auth
        {"keys": {"p256dh": "k", "auth": "a"}},                  # no endpoint
    ],
)
async def test_incomplete_subscriptions_are_rejected(subscription):
    """A subscription missing any part cannot be encrypted to, so storing it
    would guarantee a failed send later."""
    from tenderradar.alerts.push import save_subscription

    class _Conn:
        async def execute(self, *args, **kwargs):  # pragma: no cover
            raise AssertionError("must not write an incomplete subscription")

    assert await save_subscription(_Conn(), 1, subscription) is False
