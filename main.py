# -*- coding: utf-8 -*-
"""
入口: python main.py
流程: 抓取(fetchers) → 信号(signals) → 三态机(state_machine) → 渲染 docs/index.html
所有 N/A 汇入数据健康台账, 渲染在看板底部。
"""
from __future__ import annotations
import json
import datetime as dt
from pathlib import Path

import yaml
from jinja2 import Template

from src import fetchers as F
from src import signals as S
from src import state_machine as SM

ROOT = Path(__file__).resolve().parent


def main():
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    th = cfg["thresholds"]
    health = []

    def track(env, label):
        health.append({"status": env["status"], "source": f"{label} · {env.get('source','')}",
                       "reason": env.get("reason")})
        return env

    # ---------- 抓取 ----------
    capex = {h["name"]: track(F.fetch_quarterly_capex(h["ticker"]), f"L2 capex {h['name']}")
             for h in cfg["hyperscalers"]}
    revenues = {o["name"]: track(F.fetch_quarterly_revenue(o["ticker"], with_gm=(o["metric"] == "revenue_gm")),
                                 f"L2 收入 {o['name']}")
                for o in cfg["overseas_confirm"]}
    bs = {w["name"]: track(F.fetch_balance_sheet_items(w["code"], w["name"]), f"L3 财报 {w['name']}")
          for w in cfg["a_share_watchlist"] if w["fin"]}
    turn = {w["name"]: track(F.fetch_daily_turnover(w["code"], th["crowding"]["lookback_days"]), f"L3 行情 {w['name']}")
            for w in cfg["a_share_watchlist"] if w["price"]}
    manual_env = track(F.load_manual_inputs(th["manual_staleness_days"]), "L1 手动层")
    manual = manual_env["data"] if manual_env["status"] == "OK" else {}

    # ---------- 信号 ----------
    sig_capex = S.hyperscaler_capex_signal(capex, th["hyperscaler_capex"])
    sig_rev = {n: S.revenue_slope_signal(e) for n, e in revenues.items()}
    sig_mem = S.memory_slope_signal(manual.get("memory_contract_price", {"status": "N/A", "reason": "手动层不可用"}),
                                    th["memory_slope"])
    sig_orders = S.domestic_orders_signal(bs, th["domestic_orders"])
    sig_crowd = S.crowding_signal(turn, th["crowding"])

    # ---------- 三态机 ----------
    state = SM.load_state()
    today = dt.date.today().isoformat()
    up_n = cfg["state_machine"]["upgrade_requires_consecutive"]

    # 各信号的 hit 语义(证伪方向为 hit=True): 数据 N/A → hit=None 冻结
    mem_hit = None if sig_mem.get("status") != "OK" else bool(sig_mem.get("decel_flags"))
    mem_date = sig_mem["periods"][-1] if sig_mem.get("status") == "OK" else today
    SM.update_signal(state, "L1_存储斜率减速", mem_hit, mem_date, up_n)

    capex_hit = None if sig_capex["companies_reporting"] == 0 else (sig_capex["trigger_decel"] or sig_capex["trigger_abs"])
    ok_cos = [c for c in sig_capex["per_company"].values() if c.get("status") == "OK"]
    capex_date = max((c["quarters"][-1] for c in ok_cos), default=today)
    SM.update_signal(state, "L2_云厂商capex减速", capex_hit, capex_date, up_n)

    ok_orders = [r for r in sig_orders["per_company"].values() if r.get("status") == "OK"]
    orders_hit = None if not ok_orders else (sig_orders["n_confirmed"] >= 2)   # 正向确认信号
    orders_date = max((r["report_dates"][-1] for r in ok_orders), default=today)
    SM.update_signal(state, "L3_国内扩产确认", orders_hit, orders_date, up_n)

    crowd_hit = None if sig_crowd.get("status") != "OK" else bool(sig_crowd.get("warn"))
    crowd_date = sig_crowd["dates"][-1] if sig_crowd.get("status") == "OK" else today
    SM.update_signal(state, "L3_拥挤度预警", crowd_hit, crowd_date, up_n)

    SM.save_state(state)

    # ---------- 渲染 ----------
    payload = {
        "calibration_status": th["calibration_status"],
        "states": {k: v for k, v in state.items() if not k.startswith("_")},
        "l1": {"memory": sig_mem,
               "others": {k: v for k, v in manual.items() if k != "memory_contract_price"}},
        "l2": {"hyperscalers": sig_capex, "revenues": sig_rev},
        "l3": {"orders": sig_orders, "crowding": sig_crowd},
        "health": [h for h in health if h["status"] != "OK"] or
                  [{"status": "OK", "source": "全部数据源", "reason": None}],
    }
    tpl = Template((ROOT / "src" / "template.html").read_text(encoding="utf-8"))
    html = tpl.render(title=cfg["meta"]["dashboard_title"],
                      run_at=dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
                      payload_json=json.dumps(payload, ensure_ascii=False))
    out = ROOT / "docs" / "index.html"
    out.parent.mkdir(exist_ok=True)
    out.write_text(html, encoding="utf-8")

    na = sum(1 for h in health if h["status"] == "N/A")
    print(f"[done] docs/index.html · 数据源 {len(health)} 项, N/A {na} 项")
    for h in health:
        if h["status"] == "N/A":
            print(f"  [N/A] {h['source']} — {h['reason']}")


if __name__ == "__main__":
    main()
