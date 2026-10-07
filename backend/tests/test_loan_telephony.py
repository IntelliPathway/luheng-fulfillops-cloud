import pytest
from pydantic import ValidationError

from app.loan_telephony import CallReceipt, CallState, DispatchRequest, can_dispatch, receipt_state


def request():
    return DispatchRequest(tenant_id="tenant-a", session_id="LC-1", dispatch_key="dispatch-1",
                           contact_reference="contact:test-1", policy_version=1, profile_version=1)


def receipt(**changes):
    return CallReceipt(**({"tenant_id": "tenant-a", "dispatch_key": "dispatch-1",
                          "provider_call_id": "sip-call-1", "sequence": 2, "state": "answered"} | changes))


@pytest.mark.parametrize("state", list(CallState))
def test_timeout_and_restart_cannot_redial(state):
    assert can_dispatch(state) == (state == CallState.PREPARED)


@pytest.mark.parametrize("changes", [{"tenant_id": "tenant-b"}, {"dispatch_key": "other"},
                                     {"provider_call_id": "other"}])
def test_receipt_cannot_cross_scope(changes):
    with pytest.raises(ValueError):
        receipt_state(request(), CallState.UNKNOWN, receipt(**changes), provider_call_id="sip-call-1")


def test_late_receipts_never_regress_or_reopen_call():
    assert receipt_state(request(), CallState.ANSWERED, receipt(state="ringing")) == CallState.ANSWERED
    assert receipt_state(request(), CallState.ENDED, receipt(), last_sequence=3) == CallState.ENDED
    with pytest.raises(ValueError):
        receipt_state(request(), CallState.ENDED, receipt())
    with pytest.raises(ValueError):
        receipt_state(request(), CallState.PREPARED, receipt())


def test_unknown_can_reconcile_to_completed_without_redial():
    assert receipt_state(request(), CallState.UNKNOWN, receipt(state="ended")) == CallState.ENDED


@pytest.mark.parametrize("changes", [{"sequence": True}, {"sequence": 0}, {"state": "identity_verified"},
                                     {"phone": "13800000000"}])
def test_strict_transport_does_not_accept_identity_or_raw_contact(changes):
    with pytest.raises(ValidationError):
        receipt(**changes)
