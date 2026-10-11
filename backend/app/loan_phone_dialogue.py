"""Bounded Chinese speech adapter for a fixed synthetic phone case; no business I/O.

ASR is external, intent extraction is deterministic. Unsupported/conditional speech
must clarify rather than guess. This fixture assertion is never customer identity.
"""

import re
import unicodedata
import uuid
from datetime import date, timedelta
from decimal import Decimal

from .loan_dialogue import Proposal, SyntheticLoanDialogue

NUMBER = r"(?:[0-9]+(?:\.[0-9]{1,2})?|[零〇一二两三四五六七八九十百]+)"
AMOUNT = re.compile(rf"({NUMBER})(?:元|块钱|块)")
DATE = re.compile(r"(?:(\d{4})年)?(\d{1,2})月(\d{1,2})(?:日|号)|\d{4}-\d{2}-\d{2}|今天|明天|后天")
FILLER = re.compile(
    r"(?:我|可以|打算|准备|想|先|会|还款|还|支付|在|到|再|改成|改为|改到|改|金额|日期|是|安排|好的|确认|嗯|就|要|[，。！、\s])+"
)
TERMINAL = {"paused", "ptp_confirmed", "paid_claimed", "ended"}


def chinese_integer(value):
    digits = dict(zip("零〇一二两三四五六七八九", (0, 0, 1, 2, 2, 3, 4, 5, 6, 7, 8, 9), strict=True))
    # Canonical spoken integers through 999; never accept concatenated digit guesses.
    if value in digits:
        return digits[value]
    for number in range(10, 1000):
        hundreds, rest = divmod(number, 100)
        tens, ones = divmod(rest, 10)
        names = "零一二三四五六七八九"
        spoken = (names[hundreds] + "百") if hundreds else ""
        if hundreds and rest and not tens:
            spoken += "零"
        if tens:
            spoken += (names[tens] if tens != 1 or hundreds else "") + "十"
        if ones:
            spoken += names[ones]
        if value.replace("两", "二") == spoken:
            return number
    raise ValueError("unsupported_spoken_integer")


def speech_proposal(text, *, today):
    if not isinstance(text, str) or not 1 <= len(text) <= 120:
        return Proposal("unclear")
    value = unicodedata.normalize("NFKC", text).strip()
    compact = re.sub(r"[，。！？、\s]", "", value)
    phrases = {
        "wrong_person": {"打错了", "你打错了", "我不是本人", "不是本人"},
        "dispute": {"我有异议", "这笔钱有异议", "这笔欠款不对"},
        "complaint": {"我要投诉", "投诉", "我要投诉你们"},
        "hardship": {"我失业了", "我有困难", "无力偿还", "暂时无力偿还"},
        "human_requested": {"转人工", "我要人工", "请转人工", "我要找人工"},
        "paid_claimed": {"我已经还了", "已经还款", "我已经还款了"},
        "identity_claim": {"我是本人", "是本人"},
        "deny_promise": {"不对", "不是", "取消", "先不记录", "不确认", "不要记录", "取消这个安排"},
        "confirm_promise": {"对", "对的", "没错", "确认", "是的", "确认还款", "确认这个安排"},
    }
    for intent, aliases in phrases.items():
        if compact in aliases:
            return Proposal(intent)
    if re.search(r"如果|可能|大概|尽量|不确定|看情况|能不能|吗|要是|或者|不是|不还|不想|[?？]", value):
        return Proposal("unclear")
    amounts, dates = list(AMOUNT.finditer(value)), list(DATE.finditer(value))
    if len(amounts) > 1 or len(dates) > 1 or not (amounts or dates):
        return Proposal("unclear")
    remainder = AMOUNT.sub("", DATE.sub("", value))
    if FILLER.sub("", remainder):
        return Proposal("unclear")
    try:
        amount = None
        if amounts:
            raw = amounts[0][1]
            amount = int(Decimal(raw) * 100) if raw[0].isdigit() else chinese_integer(raw) * 100
        due = None
        if dates:
            match, raw = dates[0], dates[0][0]
            if raw in {"今天", "明天", "后天"}:
                due = today + timedelta(days={"今天": 0, "明天": 1, "后天": 2}[raw])
            elif "-" in raw:
                due = date.fromisoformat(raw)
            else:
                due = date(int(match[1] or today.year), int(match[2]), int(match[3]))
        return Proposal("offer_promise", amount, due.isoformat() if due else None)
    except (ValueError, ArithmeticError):
        return Proposal("unclear")


class SyntheticPhoneDialogue:
    def __init__(self, *, today, verified_fixture=False):
        self.state = SyntheticLoanDialogue(today=today)
        self.session_id = uuid.uuid4().hex
        if verified_fixture is True:
            self.state.verify_fixture(expected_version=self.state.version)

    def advance(self, text):
        proposal = speech_proposal(text, today=self.state.today)
        if self.state.stage == "ptp_confirmed" and proposal.intent != "confirm_promise":
            # A phone candidate is still a draft; later corrections/withdrawals invalidate it.
            self.cancel(include_confirmed=True)
        if self.state.stage in TERMINAL:
            return self.state.output(
                "terminal", "本次合成结果已确定，请交给人工核实。结束时先说结束测试，再说确认结束测试。"
            )
        return self.state.advance(proposal, expected_version=self.state.version)

    def cancel(self, *, include_confirmed=False):
        if (
            self.state.stage == "promise_pending_confirmation"
            or include_confirmed
            and self.state.stage == "ptp_confirmed"
        ):
            self.state.stage = "collecting_promise"
            self.state.version += 1

    def result(self):
        return {
            "schema_version": 1,
            "mode": "synthetic_phone_dialogue",
            "fixture_case": "FIXTURE-LOAN-001",
            "session_id": self.session_id,
            "as_of_date": self.state.today.isoformat(),
            "stage": self.state.stage,
            "verification": "synthetic_fixture" if self.state.verified else "unverified",
            "candidate": (
                {"amount_cents": self.state.amount, "due_date": self.state.due}
                if self.state.verified and self.state.stage == "ptp_confirmed"
                else None
            ),
            "phone_audio_verified": False,
            "business_ready": False,
        }


def validate_phone_result(value):
    keys = {
        "schema_version",
        "mode",
        "fixture_case",
        "session_id",
        "as_of_date",
        "stage",
        "verification",
        "candidate",
        "phone_audio_verified",
        "business_ready",
    }
    if (
        not isinstance(value, dict)
        or set(value) != keys
        or type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or value["mode"] != "synthetic_phone_dialogue"
        or value["fixture_case"] != "FIXTURE-LOAN-001"
        or value["business_ready"] is not False
        or value["phone_audio_verified"] is not False
        or not isinstance(value["session_id"], str)
        or not re.fullmatch(r"[a-f0-9]{32}", value["session_id"])
        or value["verification"] not in {"unverified", "synthetic_fixture"}
        or value["stage"]
        not in TERMINAL | {"identity_pending", "debt_explained", "collecting_promise", "promise_pending_confirmation"}
    ):
        raise ValueError("invalid_synthetic_phone_result")
    today = date.fromisoformat(value["as_of_date"])
    if today.isoformat() != value["as_of_date"]:
        raise ValueError("invalid_synthetic_phone_result")
    candidate = value["candidate"]
    if candidate is not None:
        if (
            not isinstance(candidate, dict)
            or set(candidate) != {"amount_cents", "due_date"}
            or type(candidate["amount_cents"]) is not int
            or not 1 <= candidate["amount_cents"] <= 10000
            or value["verification"] != "synthetic_fixture"
            or value["stage"] != "ptp_confirmed"
        ):
            raise ValueError("invalid_synthetic_phone_result")
        due = date.fromisoformat(candidate["due_date"])
        if due.isoformat() != candidate["due_date"] or not today <= due <= today + timedelta(days=30):
            raise ValueError("invalid_synthetic_phone_result")
    elif value["stage"] == "ptp_confirmed":
        raise ValueError("invalid_synthetic_phone_result")
    return value
