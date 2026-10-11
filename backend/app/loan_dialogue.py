"""Offline structured-intent contract, fixed synthetic case only; no ASR, model or writes.

A statement of identity never authenticates a person. verify_fixture is an explicit
sandbox assertion. event_candidate must still pass existing tenant/session gates.
"""

from dataclasses import dataclass
from datetime import date, timedelta

EXCEPTIONS = frozenset({"wrong_person", "dispute", "complaint", "hardship", "human_requested"})
INTENTS = EXCEPTIONS | {
    "identity_claim",
    "offer_promise",
    "confirm_promise",
    "deny_promise",
    "paid_claimed",
    "unclear",
    "end",
}


class DialogueError(ValueError):
    pass


@dataclass(frozen=True)
class Proposal:
    intent: str
    amount_cents: int | None = None
    due_date: str | None = None
    confidence: str = "high"


class SyntheticLoanDialogue:
    """Only FIXTURE-LOAN-001 / 100 yuan. No configurable customer facts."""

    def __init__(self, *, today: date):
        if type(today) is not date:
            raise DialogueError("invalid_fixture_date")
        self.today = today
        self.version = 1
        self.stage = "identity_pending"
        self.turns = 0
        self.amount = None
        self.due = None
        self.clarifications = 0
        self.verified = False

    def check(self, expected_version):
        if type(expected_version) is not int or expected_version != self.version:
            raise DialogueError("stale_dialogue_version")
        if self.stage in {"paused", "ptp_confirmed", "paid_claimed", "ended"}:
            raise DialogueError("dialogue_terminal")

    def clear(self):
        self.amount = self.due = None

    def output(self, action, reply, event=None):
        return {
            "mode": "synthetic_loan_dialogue",
            "case": "FIXTURE-LOAN-001",
            "version": self.version,
            "stage": self.stage,
            "action": action,
            "reply": reply,
            "verification": "synthetic_fixture" if self.verified else "unverified",
            "candidate": (
                {"amount_cents": self.amount, "due_date": self.due}
                if self.verified and (self.amount is not None or self.due is not None)
                else None
            ),
            "event_candidate": event,
            "external_calls": 0,
            "business_ready": False,
        }

    def verify_fixture(self, *, expected_version):
        self.check(expected_version)
        if self.stage != "identity_pending":
            raise DialogueError("fixture_already_verified")
        self.verified = True
        self.stage = "debt_explained"
        self.version += 1
        return self.output(
            "explain_fixture_debt", "这是合成案件，待还金额100元。您打算怎么安排？", {"intent": "identity_verified"}
        )

    def clarify(self, reply):
        self.clarifications += 1
        if self.clarifications >= 3:
            self.clear()
            self.stage = "paused"
            return self.output("handoff", "连续几次没确认清楚，我们暂停，交给人工核实。")
        return self.output("clarify", reply)

    def advance(self, proposal: Proposal, *, expected_version, context_current=True):
        self.check(expected_version)
        if type(context_current) is not bool or type(proposal) is not Proposal:
            raise DialogueError("invalid_dialogue_input")
        if (
            not isinstance(proposal.intent, str)
            or proposal.intent not in INTENTS
            or not isinstance(proposal.confidence, str)
            or proposal.confidence not in {"high", "uncertain"}
        ):
            raise DialogueError("invalid_dialogue_intent")
        # Validate slot wire types before consuming a turn. Never coerce bool/float/string money.
        if proposal.amount_cents is not None and type(proposal.amount_cents) is not int:
            raise DialogueError("invalid_amount_type")
        if proposal.due_date is not None and type(proposal.due_date) is not str:
            raise DialogueError("invalid_due_date_type")
        self.version += 1
        self.turns += 1
        if not context_current or self.turns > 12:
            self.clear()
            self.stage = "paused"
            return self.output("pause", "本次对话已暂停，请人工核实后再继续。")
        if proposal.confidence == "uncertain" or proposal.intent == "unclear":
            # A doubtful turn invalidates a pending readback confirmation.
            if self.stage == "promise_pending_confirmation":
                self.stage = "collecting_promise"
            return self.clarify("我没听清您的意思，麻烦再说一遍。")
        if proposal.intent in EXCEPTIONS:
            self.clear()
            self.stage = "paused"
            return self.output("handoff", "好的，我们先暂停，交给人工核实。", {"intent": proposal.intent})
        if proposal.intent == "end":
            self.clear()
            self.stage = "ended"
            return self.output("end", "好的，本次合成对话结束。", {"intent": "end"})
        if not self.verified:
            self.clear()
            return self.output("request_verification", "需要先完成身份核验，再说明具体事项。")
        if proposal.intent == "paid_claimed":
            self.clear()
            self.stage = "paid_claimed"
            return self.output("verify_payment", "收到，我们先核对到账记录。", {"intent": "paid_claimed"})
        if proposal.intent == "deny_promise":
            self.clear()
            self.stage = "debt_explained"
            self.clarifications = 0
            return self.output("ask_arrangement", "好，先不记录。您希望怎么安排？")
        if proposal.intent == "confirm_promise" and proposal.amount_cents is None and proposal.due_date is None:
            if self.stage != "promise_pending_confirmation":
                return self.clarify("还没有可以确认的计划，请先说明金额和日期。")
            self.stage = "ptp_confirmed"
            return self.output(
                "propose_record_promise",
                "好的，这次记录的是还款承诺，到账后再核对。",
                {"intent": "promise", "confirmed": True, "amount_cents": self.amount, "due_date": self.due},
            )
        if proposal.intent not in {"offer_promise", "confirm_promise"}:
            return self.clarify("您打算还多少，安排在哪一天？")
        # Any modification (even embedded in a confirmation) requires a fresh readback.
        self.stage = "collecting_promise"
        if proposal.amount_cents is not None:
            if not 0 < proposal.amount_cents <= 10_000:
                self.amount = None
                return self.clarify("请确认金额，不能超过这笔合成案件的100元。")
            self.amount = proposal.amount_cents
        if proposal.due_date is not None:
            try:
                due = date.fromisoformat(proposal.due_date)
            except ValueError:
                self.due = None
                return self.clarify("请给出明确日期，例如2026年10月12日。")
            if due.isoformat() != proposal.due_date or not self.today <= due <= self.today + timedelta(days=30):
                self.due = None
                return self.clarify("请确认日期，需要在今天起30天内。")
            self.due = due.isoformat()
        if self.amount is None:
            return self.output("ask_amount", "您打算先还多少元？")
        if self.due is None:
            return self.output("ask_date", "您打算在哪一天还？请说具体日期。")
        self.clarifications = 0
        self.stage = "promise_pending_confirmation"
        amount = f"{self.amount // 100}元" if self.amount % 100 == 0 else f"{self.amount // 100}.{self.amount % 100:02d}元"
        due = date.fromisoformat(self.due)
        return self.output("readback_promise", f"我确认一下，{due.year}年{due.month}月{due.day}日还{amount}，对吗？")
