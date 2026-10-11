from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from test_loan_collection import BASE, H, configure_policy, enroll, event, start
from test_loan_collection import client as client

from app.loan_phone_dialogue import SyntheticPhoneDialogue
from app.models import RecoveryLedgerEntry


def proposal():
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    dialogue = SyntheticPhoneDialogue(today=today, verified_fixture=True)
    dialogue.advance("我明天还50元")
    dialogue.advance("确认")
    return dialogue.result()


def test_phone_draft_uses_current_business_version_identity_scope_and_idempotency(client):
    assert enroll(client).status_code == 200
    row = start(client).json()
    draft = proposal()
    body = {
        "event_key": "phone-" + draft["session_id"] + "-" + row["id"],
        "expected_version": row["version"],
        "acknowledged": True,
        "intent": "promise",
        "confirmed": True,
        **draft["candidate"],
    }
    url = BASE + f"/sessions/{row['id']}/events"
    assert client.post(url, headers=H, json=body).status_code == 422
    assert client.post(url, headers=H | {"X-Tenant-ID": "TENANT_B"}, json=body).status_code == 404
    assert client.post(url, headers=H | {"X-Actor-ID": "test-viewer"}, json=body).status_code == 403
    verified = event(client, row, "identity_verified").json()
    assert client.post(url, headers=H, json=body).status_code == 409
    body["expected_version"] = verified["version"]
    with client.app.state.Session() as db:
        before = db.scalar(select(func.count()).select_from(RecoveryLedgerEntry))
    recorded = client.post(url, headers=H, json=body)
    assert recorded.status_code == 200, recorded.text
    assert recorded.json()["state"] == "ptp_recorded"
    assert recorded.json()["promise"]["amount_cents"] == 5000
    assert recorded.json()["promise"]["status"] == "pending"
    assert client.post(url, headers=H, json=body).json() == recorded.json()
    with client.app.state.Session() as db:
        assert db.scalar(select(func.count()).select_from(RecoveryLedgerEntry)) == before


def test_policy_revision_blocks_even_a_confirmed_phone_draft(client):
    enroll(client)
    row = event(client, start(client).json(), "identity_verified").json()
    draft = proposal()
    assert configure_policy(client, expected_version=1, paused=True).status_code == 200
    response = event(client, row, "promise", confirmed=True, **draft["candidate"])
    assert response.status_code == 200
    assert response.json()["state"] == "paused"
    assert response.json()["promise"] == {}
