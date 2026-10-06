import hashlib
import json

from test_case_acceptance import create, ledger_case, prepared, review
from test_customer_materials import headers

from app.models import PaymentReceipt

__all__ = ["ledger_case"]


def test_integrated_report_is_scoped_fresh_and_never_grants_execution(ledger_case):
    client, _, secret = ledger_case
    path = "/api/v1/pilot/integration-acceptance"
    assert client.get(path).status_code == 401
    link = prepared(client, secret, production=True)
    row, _ = create(client, link)
    assert review(client, row).status_code == 200
    response = client.get(path, headers=headers())
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    report = response.json()
    assert report["app_version"] == "5.0.0"
    assert report["customer_cases"]["current_accepted"] == 1
    assert report["status"] == "blocked"
    assert not report["real_business_verified"] and not report["enables_external_execution"]
    digest = report.pop("report_digest")
    assert digest == hashlib.sha256(json.dumps(report, sort_keys=True).encode()).hexdigest()
    other = client.get(path, headers=headers("test-viewer", "TENANT_B")).json()
    assert other["customer_cases"]["current_accepted"] == 0
    with client.app.state.Session() as db:
        receipt = db.query(PaymentReceipt).filter_by(tenant_id="TENANT_A", provider_event_id="REPORT-PAYMENT").one()
        receipt.signature_verified = False
        db.commit()
    stale = client.get(path, headers=headers()).json()
    assert stale["customer_cases"] == {"current_accepted": 0, "invalidated_accepted": 1}
    assert not next(c["passed"] for c in stale["checks"] if c["id"] == "customer")
    assert stale["report_digest"] != digest
    assert client.post(path, headers=headers(), json={"approve": True}).status_code == 405
