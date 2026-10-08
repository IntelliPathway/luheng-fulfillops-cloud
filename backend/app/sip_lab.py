"""Host-side SIP lab tool. Fixed local gateway/extension, never customer collection."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

ORIGIN = "http://127.0.0.1:8088/ari"
KEY = re.compile(r"[A-Za-z0-9_.-]{1,80}\Z")
CHANNEL_STATES = {"Down", "Rsrvd", "OffHook", "Dialing", "Ring", "Ringing", "Up", "Busy", "Dialing Offhook", "Pre-ring"}


class SIPLabError(RuntimeError):
    pass


@dataclass(frozen=True)
class LabConfig:
    instance: str
    password: str

    @classmethod
    def from_environment(cls):
        if os.getenv("APP_ENV", "").lower() not in {"development", "test"}:
            raise SIPLabError("SIP 实验室仅允许 development/test，不允许生产环境")
        if os.getenv("ENABLE_SIP_LAB", "false").lower() != "true":
            raise SIPLabError("SIP 实验室未显式启用")
        instance = os.getenv("SIP_LAB_INSTANCE_ID", "")
        password = os.getenv("SIP_LAB_ARI_PASSWORD", "")
        if not re.fullmatch(r"[a-f0-9]{32}", instance) or not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", password):
            raise SIPLabError("实验室实例或 ARI 凭据无效，请生成独立配置")
        return cls(instance, password)


class CallJournal:
    """Reserve before I/O; lost responses and restarts cannot resend the same intent."""
    def __init__(self, path):
        path = Path(path)
        if not path.exists():
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        self.db = sqlite3.connect(path, timeout=10)
        self.db.execute("CREATE TABLE IF NOT EXISTS sip_lab_calls (instance TEXT NOT NULL, request_key TEXT NOT NULL, "
                        "channel_id TEXT NOT NULL, state TEXT NOT NULL, created_day TEXT NOT NULL, "
                        "PRIMARY KEY(instance, request_key))")
        self.db.commit()

    def reserve(self, instance, key):
        if not KEY.fullmatch(key):
            raise SIPLabError("请求键必须为 1–80 个字母、数字、点、下划线或连字符")
        channel = "RG-LAB-" + hashlib.sha256(f"{instance}:{key}".encode()).hexdigest()[:32]
        day = datetime.now(UTC).date().isoformat()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.lookup(instance, key)
            if row:
                self.db.commit()
                return row, False
            count = self.db.execute("SELECT count(*) FROM sip_lab_calls WHERE instance=? AND created_day=?",
                                    (instance, day)).fetchone()[0]
            if count >= 20:
                raise SIPLabError("同一实验室 UTC 日测试上限 20 次；未知结果也占用次数")
            self.db.execute("INSERT INTO sip_lab_calls VALUES (?,?,?,?,?)", (instance, key, channel, "reserved", day))
            self.db.commit()
            return {"channel_id": channel, "dispatch_state": "reserved"}, True
        except BaseException:
            self.db.rollback()
            raise

    def lookup(self, instance, key):
        row = self.db.execute("SELECT channel_id,state FROM sip_lab_calls WHERE instance=? AND request_key=?",
                              (instance, key)).fetchone()
        return {"channel_id": row[0], "dispatch_state": row[1]} if row else None

    def mark(self, instance, key, state):
        self.db.execute("UPDATE sip_lab_calls SET state=? WHERE instance=? AND request_key=?", (state, instance, key))
        self.db.commit()

    def close(self):
        self.db.close()


class SIPLab:
    def __init__(self, config, journal, transport=None):
        self.config = config
        self.journal = journal
        self.http = httpx.Client(base_url=ORIGIN, auth=("repayguard_lab", config.password),
                                 timeout=5, follow_redirects=False, trust_env=False, transport=transport)

    @staticmethod
    def view(**values):
        return {"mode": "sip_lab", "target": "linphone_1001", "audio_verified": False,
                "ai_dialogue_ready": False, "pstn_enabled": False, **values}

    def endpoint_status(self):
        try:
            response = self.http.get("/endpoints/PJSIP/1001")
            if response.status_code != 200:
                return self.view(gateway_reachable=False, endpoint_state="unavailable")
            data = response.json()
            if data.get("technology") != "PJSIP" or data.get("resource") != "1001":
                raise ValueError("scope")
            state = data.get("state")
            return self.view(gateway_reachable=True, endpoint_state=state if isinstance(state, str) and state in {"online", "offline", "unknown"} else "unknown")
        except (httpx.HTTPError, ValueError, AttributeError):
            return self.view(gateway_reachable=False, endpoint_state="unknown")

    def inspect(self, key):
        row = self.journal.lookup(self.config.instance, key)
        if not row:
            raise SIPLabError("此实例没有对应请求；不查询任意通话 ID")
        try:
            response = self.http.get(f"/channels/{row['channel_id']}")
            if response.status_code == 404:
                return self.view(**row, channel_state="not_found_or_ended", retry_allowed=False)
            if response.status_code == 200:
                data = response.json()
                if data.get("id") == row["channel_id"]:
                    state = data.get("state")
                    return self.view(**row, channel_state=state if isinstance(state, str) and state in CHANNEL_STATES else "unknown", retry_allowed=False)
        except (httpx.HTTPError, ValueError, AttributeError):
            pass
        return self.view(**row, channel_state="unknown", retry_allowed=False)

    def call(self, key, *, acknowledged=False):
        if not acknowledged:
            raise SIPLabError("必须确认只联系自己的测试分机、不使用客户数据")
        row, created = self.journal.reserve(self.config.instance, key)
        if not created:
            return self.inspect(key)
        try:
            response = self.http.post(f"/channels/{row['channel_id']}", params={
                "endpoint": "PJSIP/1001", "context": "lab-echo", "extension": "1000",
                "priority": 1, "callerId": "RepayGuard SIP Lab <1000>", "timeout": 20,
            })
            state = "rejected" if response.status_code in {400, 401, 403, 404} else "unknown"
            if response.status_code in {200, 201} and response.json().get("id") == row["channel_id"]:
                state = "submitted"
        except (httpx.HTTPError, ValueError, AttributeError):
            state = "unknown"
        self.journal.mark(self.config.instance, key, state)
        return self.view(channel_id=row["channel_id"], dispatch_state=state, retry_allowed=False)

    def hangup(self, key):
        """Stop only this journal's channel; never infer completion from a 404."""
        row = self.journal.lookup(self.config.instance, key)
        if not row:
            raise SIPLabError("此实例没有对应请求；不能挂断任意通话 ID")
        outcome = "unknown"
        try:
            response = self.http.delete(f"/channels/{row['channel_id']}")
            if response.status_code == 204:
                outcome = "requested"
            elif response.status_code == 404:
                outcome = "not_found_or_ended"
        except httpx.HTTPError:
            pass
        # Preserve dispatch history and no-redial reservation even after hangup.
        return self.view(**row, hangup_state=outcome, retry_allowed=False)

    def close(self):
        self.http.close()


def main():
    parser = argparse.ArgumentParser(description="Linphone 1001 ↔ Asterisk 本机 SIP 回声联调")
    parser.add_argument("action", choices=["status", "doctor", "call", "inspect", "hangup"])
    parser.add_argument("--request-key")
    parser.add_argument("--journal", default="sip-lab-calls.db")
    parser.add_argument("--acknowledged", action="store_true")
    parser.add_argument("--config-dir", default=str(Path(__file__).resolve().parents[2] / "deploy/sip-lab/generated"))
    args = parser.parse_args()
    lab = None
    journal = None
    try:
        config = LabConfig.from_environment()
        if args.action not in {"status", "doctor"} and not args.request_key:
            raise SIPLabError("call/inspect/hangup 必须提供稳定 request-key")
        if args.action not in {"status", "doctor"}:
            journal = CallJournal(args.journal)
        lab = SIPLab(config, journal)
        if args.action == "status":
            result = lab.endpoint_status()
        elif args.action == "doctor":
            from .sip_lab_diagnostics import configuration_checks
            result = {**lab.endpoint_status(), **configuration_checks(args.config_dir)}
            result["ready_for_echo_attempt"] = bool(result["configuration_ready"]
                                                   and result["gateway_reachable"]
                                                   and result["endpoint_state"] == "online")
        elif args.action == "call":
            result = lab.call(args.request_key, acknowledged=args.acknowledged)
        elif args.action == "hangup":
            result = lab.hangup(args.request_key)
        else:
            result = lab.inspect(args.request_key)
        print(json.dumps(result, ensure_ascii=False))
        if args.action == "doctor":
            return 0 if result["ready_for_echo_attempt"] else 2
        if args.action == "status":
            return 0 if result["gateway_reachable"] and result["endpoint_state"] == "online" else 2
        if args.action == "call":
            return 0 if result["dispatch_state"] == "submitted" else 2
        if args.action == "hangup":
            return 0 if result["hangup_state"] == "requested" else 2
        return 0 if result["channel_state"] in CHANNEL_STATES else 2
    except (SIPLabError, OSError, sqlite3.Error):
        # Do not echo config, HTTP exceptions, file contents or credentials.
        print(json.dumps({"error": "SIP 实验室操作被阻断，请检查开发模式、生成配置、请求键及实验室日上限"}, ensure_ascii=False))
        return 1
    finally:
        if lab:
            lab.close()
        if journal:
            journal.close()


if __name__ == "__main__":
    raise SystemExit(main())
