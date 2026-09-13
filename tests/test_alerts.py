"""Digest tokens and rendering.

Feedback links authenticate themselves with no login, so forging one must be
impossible: feedback is the table match precision is computed from, and
precision is the Phase 2 gate.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tenderradar.alerts import tokens
from tenderradar.alerts.digest import Digest, DigestItem
from tenderradar.alerts.sender import build_message
from tenderradar.config import settings


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setattr(settings, "alert_token_secret", "test-secret-for-tests")


# ---------------------------------------------------------------- tokens


def test_feedback_token_round_trips():
    token = tokens.feedback_token(7, 42, "up")
    assert tokens.parse_feedback_token(token) == (7, 42, "up")


def test_flipping_the_verdict_invalidates_the_token():
    """Otherwise anyone could turn a thumbs-down into a thumbs-up."""
    token = tokens.feedback_token(7, 42, "up")
    assert tokens.parse_feedback_token(token.replace(":up:", ":down:")) is None


def test_changing_the_user_invalidates_the_token():
    """A token must not let one user vote as another."""
    token = tokens.feedback_token(7, 42, "up")
    assert tokens.parse_feedback_token(token.replace("fb:7:", "fb:8:")) is None


def test_changing_the_tender_invalidates_the_token():
    token = tokens.feedback_token(7, 42, "up")
    assert tokens.parse_feedback_token(token.replace(":42:", ":43:")) is None


def test_a_forged_signature_is_rejected():
    assert tokens.parse_feedback_token("fb:7:42:up:AAAAAAAAAAAAAAAAAAAAAA") is None


def test_garbage_is_rejected_without_raising():
    for bad in ("", "nonsense", "fb:", "fb:7:42", "::::"):
        assert tokens.parse_feedback_token(bad) is None


def test_token_kinds_are_not_interchangeable():
    """An unsubscribe token must not work as a feedback token, or vice versa."""
    unsub = tokens.unsubscribe_token(7)
    assert tokens.parse_feedback_token(unsub) is None
    assert tokens.parse_open_token(unsub) is None
    assert tokens.parse_unsubscribe_token(unsub) == 7


def test_a_different_secret_invalidates_every_token(monkeypatch):
    token = tokens.feedback_token(7, 42, "up")
    monkeypatch.setattr(settings, "alert_token_secret", "a-different-secret")
    assert tokens.parse_feedback_token(token) is None


def test_only_known_verdicts_can_be_signed():
    with pytest.raises(ValueError):
        tokens.feedback_token(7, 42, "maybe")


def test_tokens_are_url_safe():
    """They go in an email link; a '+' or '/' would break on some clients."""
    token = tokens.feedback_token(7, 42, "up")
    assert all(ch.isalnum() or ch in "-_:" for ch in token)


def test_missing_secret_fails_loudly(monkeypatch):
    """Silently signing with an empty key would make every token forgeable."""
    monkeypatch.setattr(settings, "alert_token_secret", "")
    with pytest.raises(RuntimeError, match="ALERT_TOKEN_SECRET"):
        tokens.feedback_token(7, 42, "up")


# --------------------------------------------------------------- digest


def make_item(tender_id: int = 1, title: str = "Deep tube well") -> DigestItem:
    return DigestItem(
        tender={
            "tender_id": tender_id,
            "title": title,
            "organization_path": "DPHE Sylhet",
            "district_name": "Sylhet",
            "procurement_nature": "works",
            "procurement_method": "OTM",
            "tender_security": None,
            "closing_at": datetime.now(UTC) + timedelta(days=9),
        },
        score=0.9,
        matched_by="both",
        similarity=0.8,
        up_url="https://example.test/f/up",
        down_url="https://example.test/f/down",
        tender_url="https://example.test/tenders/1",
    )


def test_empty_digest_is_detected():
    """Sending an empty digest teaches people the mail is worthless."""
    assert Digest(user={"id": 1}).is_empty


def test_subject_counts_tenders_and_names_the_company():
    digest = Digest(user={"id": 1, "company_name": "Karim Electricals"})
    digest.items = [make_item(1), make_item(2)]
    assert digest.subject == "2 new tenders for Karim Electricals"


def test_subject_is_singular_for_one_tender():
    digest = Digest(user={"id": 1, "company_name": "Karim Electricals"})
    digest.items = [make_item()]
    assert digest.subject.startswith("1 new tender for")


def test_subject_survives_a_missing_company_name():
    digest = Digest(user={"id": 1})
    digest.items = [make_item()]
    assert "you" in digest.subject


# --------------------------------------------------------------- message


def test_message_carries_both_a_text_and_an_html_part():
    """A digest with no text part looks like spam to most filters."""
    message = build_message(
        to="a@example.test", subject="s", html="<p>hi</p>", text="hi"
    )
    types = {part.get_content_type() for part in message.walk()}
    assert "text/plain" in types
    assert "text/html" in types


def test_one_click_unsubscribe_headers_are_set():
    """Gmail and Outlook then show a native unsubscribe instead of the spam
    button, and spam reports are what get a young sending domain blocked."""
    message = build_message(
        to="a@example.test", subject="s", html="<p>hi</p>", text="hi",
        unsubscribe_url="https://example.test/unsubscribe/tok",
    )
    assert message["List-Unsubscribe"] == "<https://example.test/unsubscribe/tok>"
    assert message["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"


def test_unsubscribe_headers_are_omitted_when_no_url_is_given():
    message = build_message(
        to="a@example.test", subject="s", html="<p>hi</p>", text="hi"
    )
    assert message["List-Unsubscribe"] is None
