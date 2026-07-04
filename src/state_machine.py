# -*- coding: utf-8 -*-
"""
三态信号机: OBSERVE → CANDIDATE → ACTIONABLE
不对称规则: 升级需连续证据(consecutive_hits), 降级即时。
幂等护栏: 同一 data_date 重跑不重复累计计数 (last_data_date guard, 沿用 ABCD v3.5 设计)。
状态持久化: data/state.json
"""
from __future__ import annotations
import json
import datetime as dt
from pathlib import Path

STATE_PATH = Path(__file__).resolve().parent.parent / "data" / "state.json"
STATES = ["OBSERVE", "CANDIDATE", "ACTIONABLE"]


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            # 损坏的状态文件按显式事故处理: 重置并留痕, 不静默吞掉
            return {"_corrupt_reset_at": dt.datetime.now().isoformat(timespec="seconds")}
    return {}


def save_state(state: dict):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def update_signal(state: dict, signal_id: str, hit: bool | None, data_date: str,
                  upgrade_requires: int = 2) -> dict:
    """
    hit=True  → 证据命中; hit=False → 证据反向(即时降级); hit=None → 数据 N/A, 状态冻结不动。
    返回该信号的当前记录。
    """
    rec = state.setdefault(signal_id, {
        "state": "OBSERVE", "consecutive_hits": 0,
        "last_data_date": None, "history": [],
    })

    if hit is None:
        rec["frozen_reason"] = "数据 N/A, 状态冻结"
        return rec
    rec.pop("frozen_reason", None)

    # 幂等护栏: data_date 未推进 → 不累计、不转移
    if rec["last_data_date"] is not None and data_date <= rec["last_data_date"]:
        return rec
    rec["last_data_date"] = data_date

    prev = rec["state"]
    if hit:
        rec["consecutive_hits"] += 1
        if rec["consecutive_hits"] >= upgrade_requires and prev != "ACTIONABLE":
            rec["state"] = STATES[min(STATES.index(prev) + 1, 2)]
            rec["consecutive_hits"] = 0
    else:
        rec["consecutive_hits"] = 0
        if prev != "OBSERVE":
            rec["state"] = "OBSERVE"   # 降级即时且直接归零, 不逐级退

    if rec["state"] != prev:
        rec["history"].append({"date": data_date, "from": prev, "to": rec["state"]})
        rec["history"] = rec["history"][-20:]
    return rec
