"""Replay fixed structured synthetic intents without credentials, Docker or inference."""

import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.loan_dialogue import DialogueError, Proposal, SyntheticLoanDialogue


def replay():
    results = []

    def scenario(name, check):
        try:
            check()
            results.append({"scenario": name, "passed": True})
        except (AssertionError, DialogueError):
            results.append({"scenario": name, "passed": False})

    def fresh():
        return SyntheticLoanDialogue(today=date(2026, 10, 11))

    def send(d, intent, **slots):
        return d.advance(Proposal(intent, **slots), expected_version=d.version)

    def prepared():
        d = fresh()
        d.verify_fixture(expected_version=d.version)
        return d

    def unverified():
        d = fresh()
        r = send(d, "identity_claim")
        if not (r["verification"] == "unverified" and r["candidate"] is None):
            raise AssertionError("synthetic invariant failed")
        r = send(d, "offer_promise", amount_cents=5000, due_date="2026-10-12")
        if not (r["event_candidate"] is None and r["action"] == "request_verification"):
            raise AssertionError("synthetic invariant failed")

    def confirm():
        d = prepared()
        if not send(d, "offer_promise", amount_cents=5000)["action"] == "ask_date":
            raise AssertionError("synthetic invariant failed")
        r = send(d, "offer_promise", due_date="2026-10-12")
        if not (r["action"] == "readback_promise" and r["event_candidate"] is None):
            raise AssertionError("synthetic invariant failed")
        r = send(d, "confirm_promise")
        if not r["event_candidate"] == {
            "intent": "promise",
            "confirmed": True,
            "amount_cents": 5000,
            "due_date": "2026-10-12",
        }:
            raise AssertionError("synthetic invariant failed")

    def modify():
        d = prepared()
        send(d, "offer_promise", amount_cents=5000, due_date="2026-10-12")
        old = d.version
        r = send(d, "confirm_promise", amount_cents=3000)
        if not (
            r["event_candidate"] is None and r["candidate"]["amount_cents"] == 3000
        ):
            raise AssertionError("synthetic invariant failed")
        try:
            d.advance(Proposal("confirm_promise"), expected_version=old)
        except DialogueError as error:
            if not str(error) == "stale_dialogue_version":
                raise AssertionError("synthetic invariant failed")
        else:
            raise AssertionError("stale confirmation accepted")
        if not send(d, "deny_promise")["candidate"] is None:
            raise AssertionError("synthetic invariant failed")

    def ambiguous():
        d = prepared()
        send(d, "offer_promise", amount_cents=5000, due_date="2026-10-12")
        if (
            not send(d, "confirm_promise", confidence="uncertain")["event_candidate"]
            is None
        ):
            raise AssertionError("synthetic invariant failed")
        if not send(d, "confirm_promise")["event_candidate"] is None:
            raise AssertionError("synthetic invariant failed")
        if not send(d, "unclear")["stage"] == "paused":
            raise AssertionError("synthetic invariant failed")

    def invalid_slots():
        d = prepared()
        if not send(d, "offer_promise", amount_cents=10001)["candidate"] is None:
            raise AssertionError("synthetic invariant failed")
        if (
            not send(d, "offer_promise", amount_cents=3000, due_date="tomorrow")[
                "action"
            ]
            == "clarify"
        ):
            raise AssertionError("synthetic invariant failed")
        if not send(d, "offer_promise", due_date="2027-01-01")["stage"] == "paused":
            raise AssertionError("synthetic invariant failed")

    def pause():
        d = prepared()
        send(d, "offer_promise", amount_cents=5000, due_date="2026-10-12")
        r = d.advance(
            Proposal("confirm_promise"),
            expected_version=d.version,
            context_current=False,
        )
        if not (
            r["stage"] == "paused"
            and r["candidate"] is None
            and (r["event_candidate"] is None)
        ):
            raise AssertionError("synthetic invariant failed")

    def payment():
        r = send(prepared(), "paid_claimed")
        if not (r["action"] == "verify_payment" and r["stage"] != "paid"):
            raise AssertionError("synthetic invariant failed")

    for name, check in [
        ("unverified_no_disclosure", unverified),
        ("collect_readback_confirm", confirm),
        ("modified_cancelled_stale_confirmation", modify),
        ("ambiguous_never_commits", ambiguous),
        ("invalid_financial_slots", invalid_slots),
        ("changed_context_pauses", pause),
        ("paid_claim_is_not_payment", payment),
    ]:
        scenario(name, check)
    for intent in (
        "wrong_person",
        "dispute",
        "complaint",
        "hardship",
        "human_requested",
    ):

        def exception_check(intent=intent):
            r = send(prepared(), intent)
            if not (
                r["stage"] == "paused"
                and r["action"] == "handoff"
                and (r["candidate"] is None)
            ):
                raise AssertionError("synthetic invariant failed")

        scenario(intent, exception_check)
    return {
        "mode": "synthetic_loan_dialogue_replay",
        "passed": all(r["passed"] for r in results),
        "scenarios": results,
        "external_calls": 0,
        "writes": 0,
        "phone_audio_verified": False,
        "business_ready": False,
    }


def main():
    report = replay()
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
