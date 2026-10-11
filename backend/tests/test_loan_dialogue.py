from datetime import date

import pytest

from app.loan_dialogue import DialogueError, Proposal, SyntheticLoanDialogue


def fixture():
    d = SyntheticLoanDialogue(today=date(2026, 10, 11))
    d.verify_fixture(expected_version=1)
    return d


def send(d, intent, **slots):
    return d.advance(Proposal(intent, **slots), expected_version=d.version)


@pytest.mark.parametrize("slot", [True, 1.5, "5000"])
def test_money_wire_types_cannot_coerce_or_consume_version(slot):
    d = fixture()
    with pytest.raises(DialogueError, match="invalid_amount_type"):
        send(d, "offer_promise", amount_cents=slot)
    assert d.version == 2 and d.amount is None


@pytest.mark.parametrize("due", ["2026-10-10", "2026-11-11", "20261012", "tomorrow"])
def test_invalid_date_after_pending_promise_disarms_confirmation(due):
    d = fixture()
    send(d, "offer_promise", amount_cents=5000, due_date="2026-10-12")
    r = send(d, "offer_promise", due_date=due)
    assert r["action"] == "clarify" and r["candidate"]["due_date"] is None
    assert send(d, "confirm_promise")["event_candidate"] is None


def test_version_binds_confirmation_and_modification_needs_fresh_readback():
    d = fixture()
    r = send(d, "offer_promise", amount_cents=5050, due_date="2026-10-12")
    assert "50.50元" in r["reply"] and r["event_candidate"] is None
    version = d.version
    r = send(d, "confirm_promise", amount_cents=3000)
    assert r["action"] == "readback_promise" and r["event_candidate"] is None
    with pytest.raises(DialogueError, match="stale_dialogue_version"):
        d.advance(Proposal("confirm_promise"), expected_version=version)
    r = send(d, "confirm_promise")
    assert r["event_candidate"]["amount_cents"] == 3000 and r["stage"] == "ptp_confirmed"
    with pytest.raises(DialogueError, match="dialogue_terminal"):
        send(d, "confirm_promise")


def test_doubtful_confirmation_and_context_change_do_not_commit():
    d = fixture()
    send(d, "offer_promise", amount_cents=5000, due_date="2026-10-12")
    assert send(d, "confirm_promise", confidence="uncertain")["event_candidate"] is None
    assert send(d, "confirm_promise")["event_candidate"] is None
    r = d.advance(Proposal("confirm_promise"), expected_version=d.version, context_current=False)
    assert r["candidate"] is None and r["event_candidate"] is None and r["stage"] == "paused"


def test_identity_claim_never_discloses_fixture_debt():
    d = SyntheticLoanDialogue(today=date(2026, 10, 11))
    for intent in ["identity_claim", "confirm_promise", "paid_claimed"]:
        r = send(d, intent)
        assert r["candidate"] is None and r["event_candidate"] is None
        assert "100元" not in r["reply"] and r["verification"] == "unverified"


@pytest.mark.parametrize("intent", ["wrong_person", "dispute", "complaint", "hardship", "human_requested"])
def test_exception_discards_pending_promise(intent):
    d = fixture()
    send(d, "offer_promise", amount_cents=5000, due_date="2026-10-12")
    r = send(d, intent)
    assert r["action"] == "handoff" and r["candidate"] is None
    assert r["event_candidate"] == {"intent": intent}


def test_bounded_dialogue_and_paid_claim_are_separate_from_receipt():
    d = fixture()
    for _ in range(12):
        assert send(d, "deny_promise")["event_candidate"] is None
    assert send(d, "offer_promise")["stage"] == "paused"
    r = send(fixture(), "paid_claimed")
    assert r["event_candidate"] == {"intent": "paid_claimed"} and r["stage"] == "paid_claimed"
