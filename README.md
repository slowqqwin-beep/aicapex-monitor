# AI Capex 二阶导监控看板（两长扩产链）

三层监控 + 三态信号机 + 静态 HTML（GitHub Pages），对应监控体系设计:
L1 高频斜率(手动录入) / L2 海外二阶导(yfinance 自动) / L3 国内确认(akshare 自动)。

## 数据源真实性声明（哪些自动、哪些不能）
| 序列 | 方式 | 说明 |
|---|---|---|
| 四大云厂商季度 capex、NVDA/MU 收入毛利 | 自动 yfinance | 财报后 T+1 可得 |
| A股 watchlist 合同负债/存货、日成交额 | 自动 akshare | 财报科目字段若变更会显式 N/A |
| DRAM/NAND 合约价环比 (TrendForce) | **手动** manual_inputs.json | 无免费 API，带 as_of 时效戳，>100 天标 STALE |
| SEAJ billings、韩国前20日出口 | **手动** 同上 | 同上，未录入即显示 N/A |

## 阈值状态
✅ **CALIBRATED** (2026-07-04)。各阈值均已赋初值并标注防什么, 见 `config.yaml` 注释。

## 使用
```bash
pip install -r requirements.txt
python main.py          # 生成 docs/index.html + data/state.json
```

### 首次推送到 GitHub 后的手动配置
1. 仓库 Settings → Pages → Source 选 "Deploy from a branch"
2. Branch 选 `main`(或 `master`), 目录选 `/docs`, Save
3. 确认 Actions 有 write 权限 (Settings → Actions → General → Workflow permissions → Read and write permissions)

GitHub Actions: `.github/workflows/update.yml` 已配置:
- 北京 16:30 (UTC 08:30) — A股收盘后
- 美股收盘后 (UTC 21:30)
- 每个工作日, 也可在 Actions 页面手动触发 (workflow_dispatch)

## 季度维护 (唯一不可自动化项)
`manual_inputs.json` → `memory_contract_price.series`:
- **来源**: TrendForce 合约价季报 (无免费 API)
- **频率**: 每季度一次, 通常 Q3 数据 10 月初发布
- **操作**: 追加一条 `{"period":"2026Q3","dram_qoq_pct":...,"nand_qoq_pct":...,"as_of":"YYYY-MM-DD","source":"TrendForce"}`
- **红线**: 超过 100 天未更新 → 看板顶部 L1 区域自动标 `STALE` 徽章
- 当前最新: 2026Q2 (as_of=2026-06-30), 下次截止: 2026-10-08

## 纪律实现
- N/A 纪律: 每个 fetcher 返回统一信封，失败带 reason 入数据健康台账，禁止补值/插值/部分合计冒充整体
- 水平调制: 存储斜率减速需 [环比<15% 且 前值≥30%]，环比转负走独立直通道（吸取 credit_stress_z 教训）
- 三态机: OBSERVE→CANDIDATE→ACTIONABLE，升级需连续2次证据、降级即时归零；last_data_date 幂等护栏防重跑膨胀
- 数据 N/A 时状态**冻结**并标注，不默认降级

## 待你校准（config.yaml → thresholds, 当前全部 UNCALIBRATED）
减速阈值 15/30、合同负债确认线 10%、存货确认线 15%、拥挤度分位 90、升级所需连续证据次数 2。

## 已知边界
- akshare 财报字段名可能随东财接口变更，变更时对应公司显式 N/A，需人工核对 fetchers.py 的 col_map
- yfinance 季度数不足 6 期时增速口径自动退化为 QoQ 并在看板标注
- 本看板为诊断工具，不产生交易指令
