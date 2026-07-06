# -*- coding: utf-8 -*-
"""
数据抓取层。
纪律:
  1) 每个 fetcher 返回统一信封 {"status": "OK"|"N/A", "reason": ..., "data": ...}
  2) 任何异常/空返回 → status=N/A + 明确 reason, 绝不返回估算值
  3) 本文件不做任何信号计算, 只负责取数与最小整形
"""
from __future__ import annotations
import json
import time
import datetime as dt
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent


def _retry(fn, max_retries=3, backoff=2):
    """GitHub Actions 跨境连接 akshare 偶发 reset, 最多重试 3 次。"""
    last_err = None
    for i in range(max_retries):
        try:
            return fn()
        except Exception as e:
            last_err = e
            if i < max_retries - 1:
                time.sleep(backoff ** i)
    raise last_err


def _ok(data, source: str):
    return {"status": "OK", "source": source, "fetched_at": dt.datetime.now().isoformat(timespec="seconds"), "data": data}


def _na(reason: str, source: str):
    return {"status": "N/A", "source": source, "fetched_at": dt.datetime.now().isoformat(timespec="seconds"), "reason": reason, "data": None}


# ============================================================
# L2 海外层 — yfinance
# ============================================================

def fetch_quarterly_capex(ticker: str):
    """季度资本开支 (现金流量表, 取绝对值)。返回按季度升序的 Series。"""
    try:
        import yfinance as yf
        cf = yf.Ticker(ticker).quarterly_cashflow
        if cf is None or cf.empty:
            return _na("yfinance 返回空现金流量表", f"yfinance:{ticker}")
        row = None
        for key in ("Capital Expenditure", "CapitalExpenditure", "Capital Expenditures"):
            if key in cf.index:
                row = cf.loc[key]
                break
        if row is None:
            return _na("现金流量表中无 Capital Expenditure 科目", f"yfinance:{ticker}")
        s = row.dropna().abs().sort_index()
        if len(s) < 3:
            return _na(f"capex 有效季度数不足({len(s)}<3), 无法计算二阶导", f"yfinance:{ticker}")
        return _ok({str(k.date()): float(v) for k, v in s.items()}, f"yfinance:{ticker}")
    except Exception as e:
        return _na(f"抓取异常: {type(e).__name__}: {e}", f"yfinance:{ticker}")


def fetch_quarterly_revenue(ticker: str, with_gm: bool = False):
    """季度营收 (可选毛利率)。"""
    try:
        import yfinance as yf
        inc = yf.Ticker(ticker).quarterly_income_stmt
        if inc is None or inc.empty:
            return _na("yfinance 返回空利润表", f"yfinance:{ticker}")
        if "Total Revenue" not in inc.index:
            return _na("利润表中无 Total Revenue 科目", f"yfinance:{ticker}")
        rev = inc.loc["Total Revenue"].dropna().sort_index()
        out = {"revenue": {str(k.date()): float(v) for k, v in rev.items()}}
        if with_gm and "Gross Profit" in inc.index:
            gp = inc.loc["Gross Profit"].dropna().sort_index()
            gm = (gp / rev.reindex(gp.index)).dropna() * 100
            out["gross_margin_pct"] = {str(k.date()): round(float(v), 1) for k, v in gm.items()}
        if len(rev) < 3:
            return _na(f"营收有效季度数不足({len(rev)}<3)", f"yfinance:{ticker}")
        return _ok(out, f"yfinance:{ticker}")
    except Exception as e:
        return _na(f"抓取异常: {type(e).__name__}: {e}", f"yfinance:{ticker}")


# ============================================================
# L3 国内层 — akshare
# ============================================================

def _ak_symbol(code: str) -> str:
    return ("SH" if code.startswith(("6", "9", "688")) else "SZ") + code


def fetch_balance_sheet_items(code: str, name: str):
    """合同负债 + 存货, 最近 8 期。扩产确认层的直接账面痕迹。"""
    src = f"akshare:balance_sheet:{code}"
    try:
        import akshare as ak
        df = ak.stock_balance_sheet_by_report_em(symbol=_ak_symbol(code))
        if df is None or df.empty:
            return _na("akshare 返回空资产负债表", src)
        col_map = {}
        for want, cands in {
            "contract_liab": ["CONTRACT_LIAB", "合同负债"],
            "inventory": ["INVENTORY", "存货"],
        }.items():
            for c in cands:
                if c in df.columns:
                    col_map[want] = c
                    break
        if "contract_liab" not in col_map and "inventory" not in col_map:
            return _na("未找到合同负债/存货列(akshare 字段可能变更, 需人工核对)", src)
        date_col = "REPORT_DATE" if "REPORT_DATE" in df.columns else df.columns[0]
        df = df.sort_values(date_col).tail(8)
        out = {"report_dates": [str(d)[:10] for d in df[date_col]]}
        for want, col in col_map.items():
            out[want] = [None if pd.isna(v) else float(v) for v in df[col]]
        return _ok(out, src)
    except Exception as e:
        return _na(f"抓取异常: {type(e).__name__}: {e}", src)


def fetch_daily_turnover(code: str, lookback_days: int = 300):
    """日成交额 (元), 供拥挤度分位计算。带重试 (GitHub Actions 跨境偶发 connection reset)。"""
    src = f"akshare:hist:{code}"
    try:
        import akshare as ak
        end = dt.date.today().strftime("%Y%m%d")
        start = (dt.date.today() - dt.timedelta(days=int(lookback_days * 1.6))).strftime("%Y%m%d")
        df = _retry(lambda: ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start, end_date=end, adjust=""))
        if df is None or df.empty or "成交额" not in df.columns:
            return _na("akshare 行情为空或无成交额列", src)
        df = df.tail(lookback_days)
        return _ok(
            {"dates": [str(d) for d in df["日期"]],
             "turnover": [float(v) for v in df["成交额"]],
             "close": [float(v) for v in df["收盘"]] if "收盘" in df.columns else None},
            src,
        )
    except Exception as e:
        return _na(f"抓取异常: {type(e).__name__}: {e}", src)


# ============================================================
# L1 手动层 — manual_inputs.json
# ============================================================

def load_manual_inputs(staleness_days: int):
    """读取手动录入层, 逐序列打 FRESH/STALE/N-A 标记。"""
    path = ROOT / "manual_inputs.json"
    src = "manual:manual_inputs.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _na("manual_inputs.json 不存在", src)
    except json.JSONDecodeError as e:
        return _na(f"JSON 解析失败: {e}", src)

    today = dt.date.today()
    out = {}
    for key, block in raw.items():
        if key.startswith("_"):
            continue
        series = block.get("series", [])
        if not series:
            out[key] = {"status": "N/A", "reason": "序列为空(未录入)", "unit": block.get("unit"), "series": []}
            continue
        last = max(series, key=lambda x: x.get("as_of", ""))
        try:
            age = (today - dt.date.fromisoformat(last["as_of"])).days
        except Exception:
            age = None
        freshness = "STALE" if (age is None or age > staleness_days) else "FRESH"
        out[key] = {"status": "OK", "freshness": freshness, "age_days": age,
                    "unit": block.get("unit"), "series": series}
    return _ok(out, src)
