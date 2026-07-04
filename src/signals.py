# -*- coding: utf-8 -*-
"""
信号计算层。输入 fetchers 的信封, 输出结构化信号。
纪律:
  1) 输入 status=N/A → 输出 status=N/A 原样传递 reason, 不做任何降级估算
  2) 二阶导定义: d2 = g_t - g_{t-1}, 其中 g 为同比/环比增速序列
  3) 减速信号必须经过水平调制 (吸取 credit_stress_z 教训: 纯变化率在两端尾部均失效)
"""
from __future__ import annotations


def _pct(a, b):
    if a is None or b is None or b == 0:
        return None
    return (a / b - 1) * 100


def hyperscaler_capex_signal(capex_envelopes: dict, th: dict):
    """
    输入: {name: fetcher信封}, capex 按季度升序。
    输出: 逐公司 YoY 增速 g、二阶导 d2、合计口径, 以及减速判定。
    """
    per_co, agg_by_q = {}, {}
    for name, env in capex_envelopes.items():
        if env["status"] != "OK":
            per_co[name] = {"status": "N/A", "reason": env.get("reason")}
            continue
        items = sorted(env["data"].items())
        vals = [v for _, v in items]
        qs = [q for q, _ in items]
        # YoY 需要 ≥5 期; 不足则退化为 QoQ 并显式标注口径
        basis, g = ("YoY", None)
        if len(vals) >= 6:
            g = [_pct(vals[i], vals[i - 4]) for i in range(4, len(vals))]
            g_qs = qs[4:]
        else:
            basis = "QoQ(期数不足,退化口径)"
            g = [_pct(vals[i], vals[i - 1]) for i in range(1, len(vals))]
            g_qs = qs[1:]
        g = [None if x is None else round(x, 1) for x in g]
        d2 = [None if (g[i] is None or g[i - 1] is None) else round(g[i] - g[i - 1], 1) for i in range(1, len(g))]
        decel_streak = 0
        for x in reversed(d2):
            if x is not None and x < 0:
                decel_streak += 1
            else:
                break
        qoq_abs = _pct(vals[-1], vals[-2]) if len(vals) >= 2 else None
        per_co[name] = {
            "status": "OK", "basis": basis, "quarters": g_qs,
            "growth": g, "d2": d2, "decel_streak": decel_streak,
            "latest_capex": vals[-1], "capex_qoq_abs_pct": None if qoq_abs is None else round(qoq_abs, 1),
        }
        for q, v in items:
            agg_by_q.setdefault(q, []).append(v)

    # 合计口径: 仅当四家在该季度全部有数才纳入 (缺一即 N/A, 不做部分合计冒充整体)
    full_qs = sorted(q for q, lst in agg_by_q.items() if len(lst) == len(capex_envelopes))
    agg = None
    if len(full_qs) >= 6:
        vals = [sum(agg_by_q[q]) for q in full_qs]
        g = [round(_pct(vals[i], vals[i - 4]), 1) for i in range(4, len(vals))]
        d2 = [round(g[i] - g[i - 1], 1) for i in range(1, len(g))]
        agg = {"quarters": full_qs[4:], "growth": g, "d2": d2}

    ok_cos = [c for c in per_co.values() if c.get("status") == "OK"]
    n_decel = sum(1 for c in ok_cos if c["decel_streak"] >= th["decel_quarters"])
    n_abs_neg = sum(1 for c in ok_cos if (c.get("capex_qoq_abs_pct") is not None and c["capex_qoq_abs_pct"] < 0))
    return {
        "per_company": per_co, "aggregate": agg,
        "companies_decelerating": n_decel, "companies_abs_qoq_negative": n_abs_neg,
        "companies_reporting": len(ok_cos),
        "trigger_decel": n_decel >= 2,                      # ≥2 家连续减速 → 修正方向计数器变号
        "trigger_abs": n_abs_neg >= 2 and th.get("abs_qoq_negative", True),
    }


def revenue_slope_signal(env: dict):
    if env["status"] != "OK":
        return {"status": "N/A", "reason": env.get("reason")}
    rev = sorted(env["data"]["revenue"].items())
    vals = [v for _, v in rev]
    qs = [q for q, _ in rev]
    g = [None if _pct(vals[i], vals[i - 1]) is None else round(_pct(vals[i], vals[i - 1]), 1) for i in range(1, len(vals))]
    d2 = [None if (g[i] is None or g[i - 1] is None) else round(g[i] - g[i - 1], 1) for i in range(1, len(g))]
    out = {"status": "OK", "quarters": qs[1:], "qoq_growth": g, "d2": d2}
    if "gross_margin_pct" in env["data"]:
        out["gross_margin_pct"] = env["data"]["gross_margin_pct"]
    return out


def memory_slope_signal(manual_block: dict, th: dict):
    """
    L1 存储合约价环比序列的二阶导 + 水平调制。
    高位钝化 (从+90%回落到+30%) ≠ 证伪; 触发条件: qoq < decel_qoq_below 且 前值 ≥ prev_qoq_above。
    环比转负走独立通道。
    """
    if manual_block.get("status") != "OK" or not manual_block.get("series"):
        return {"status": "N/A", "reason": manual_block.get("reason", "未录入")}
    s = sorted(manual_block["series"], key=lambda x: x["period"])
    periods = [x["period"] for x in s]
    dram = [x.get("dram_qoq_pct") for x in s]
    nand = [x.get("nand_qoq_pct") for x in s]
    out = {"status": "OK", "freshness": manual_block.get("freshness"), "age_days": manual_block.get("age_days"),
           "periods": periods, "dram_qoq": dram, "nand_qoq": nand}
    flags = []
    for label, seq in (("DRAM", dram), ("NAND", nand)):
        if len(seq) >= 2 and seq[-1] is not None and seq[-2] is not None:
            d2 = round(seq[-1] - seq[-2], 1)
            out[f"{label.lower()}_d2"] = d2
            if seq[-1] < th["negative_qoq_confirm"]:
                flags.append(f"{label} 环比转负 ({seq[-1]}%)")
            elif seq[-1] < th["decel_qoq_below"] and seq[-2] >= th["prev_qoq_above"]:
                flags.append(f"{label} 高斜率回落触发 ({seq[-2]}%→{seq[-1]}%)")
    out["decel_flags"] = flags
    return out


def domestic_orders_signal(bs_envelopes: dict, th: dict):
    """合同负债/存货(备料代理)的环比。正向确认信号。"""
    per_co = {}
    n_confirm = 0
    for name, env in bs_envelopes.items():
        if env["status"] != "OK":
            per_co[name] = {"status": "N/A", "reason": env.get("reason")}
            continue
        d = env["data"]
        row = {"status": "OK", "report_dates": d["report_dates"]}
        confirmed = False
        for field, trig_key, label in (("contract_liab", "contract_liab_qoq_min", "合同负债"),
                                       ("inventory", "inventory_accel_min", "存货")):
            seq = d.get(field)
            if not seq:
                row[field] = None
                continue
            row[field] = seq
            if len(seq) >= 2 and seq[-1] is not None and seq[-2] not in (None, 0):
                qoq = round(_pct(seq[-1], seq[-2]), 1)
                row[f"{field}_qoq_pct"] = qoq
                if qoq is not None and qoq >= th[trig_key]:
                    confirmed = True
        row["expansion_confirmed"] = confirmed
        n_confirm += int(confirmed)
        per_co[name] = row
    return {"per_company": per_co, "n_confirmed": n_confirm}


def crowding_signal(turnover_envelopes: dict, th: dict):
    """watchlist 合计成交额的 250 日分位。缺任何一家 → 剔除该家并标注, 不补值。"""
    daily = {}
    missing = []
    for name, env in turnover_envelopes.items():
        if env["status"] != "OK":
            missing.append(name)
            continue
        for d, v in zip(env["data"]["dates"], env["data"]["turnover"]):
            daily.setdefault(d, 0.0)
            daily[d] += v
    if not daily:
        return {"status": "N/A", "reason": "全部行情抓取失败: " + "; ".join(missing[:3])}
    dates = sorted(daily)[-th["lookback_days"]:]
    series = [daily[d] for d in dates]
    latest = series[-1]
    pct = round(sum(1 for v in series if v <= latest) / len(series) * 100, 1)
    return {"status": "OK", "dates": dates, "turnover_sum": series,
            "latest_pctile": pct, "warn": pct >= th["pctile_warn"],
            "excluded_for_missing_data": missing}
