"""Provider-neutral telephony contract. No network calls or business authorization.

Timeouts are ambiguous: reconcile the same dispatch key, never redial blindly.
The future persistent Worker adapter must commit this state before sending.
"""
from enum import StrEnum
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field


class CallState(StrEnum):
    PREPARED = "prepared"
    DISPATCHING = "dispatching"
    UNKNOWN = "unknown"
    ACCEPTED = "accepted"
    RINGING = "ringing"
    ANSWERED = "answered"
    ENDED = "ended"
    FAILED = "failed"


class DispatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    tenant_id: str = Field(min_length=1, max_length=80)
    session_id: str = Field(min_length=1, max_length=40)
    dispatch_key: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,80}$")
    contact_reference: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.:-]{2,159}$")
    policy_version: int = Field(strict=True, ge=1)
    profile_version: int = Field(strict=True, ge=1)
    environment: Literal["internal_test"] = "internal_test"


class CallReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    tenant_id: str = Field(min_length=1, max_length=80)
    dispatch_key: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,80}$")
    provider_call_id: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,160}$")
    sequence: int = Field(strict=True, ge=1)
    state: Literal["accepted", "ringing", "answered", "ended", "failed"]


class TelephonyProvider(Protocol):
    """An adapter must support lookup by key and idempotent dispatch.

    Receipt authentication belongs to the transport boundary. Contact resolution,
    quotas, current authority and allowlisted test endpoints precede dispatch.
    Answered never constitutes institutional identity verification.
    """

    def dispatch(self, request: DispatchRequest) -> CallReceipt: ...

    def lookup(self, tenant_id: str, dispatch_key: str) -> CallReceipt | None: ...


def receipt_state(
    request: DispatchRequest, current: CallState, receipt: CallReceipt,
    *, last_sequence: int = 0, provider_call_id: str | None = None,
) -> CallState:
    """Validate correlation and monotonic progress before persisting a receipt.

    Persistence must additionally bind event digest to sequence for replay checks.
    None from lookup is not evidence that a call was never placed.
    """
    if (receipt.tenant_id, receipt.dispatch_key) != (request.tenant_id, request.dispatch_key):
        raise ValueError("receipt scope mismatch")
    if provider_call_id is not None and receipt.provider_call_id != provider_call_id:
        raise ValueError("provider call mismatch")
    if receipt.sequence <= last_sequence:
        return current
    if current == CallState.PREPARED:
        raise ValueError("dispatch has not started")
    target = CallState(receipt.state)
    if current in {CallState.ENDED, CallState.FAILED}:
        if target != current:
            raise ValueError("conflicting terminal receipt")
        return current
    progress = {CallState.ACCEPTED: 1, CallState.RINGING: 2, CallState.ANSWERED: 3}
    if current in progress and target in progress and progress[target] < progress[current]:
        return current
    return target


def can_dispatch(state: CallState) -> bool:
    """Only a never-sent request may be dispatched without reconciliation."""
    return state == CallState.PREPARED
