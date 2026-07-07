# AI Capex 二阶导看板 · 本地每日自动更新
# 用法: powershell -ExecutionPolicy Bypass -File daily_update.ps1
$ErrorActionPreference = "Stop"

# 1. 关代理 (避开公司代理拦截东财)
$env:HTTP_PROXY = ""
$env:HTTPS_PROXY = ""

# 2. 抓数据 + 渲染
Set-Location "d:\liquidity-dashboard\A股\ai capex二阶导叙事\aicapex_monitor"
python main.py
if ($LASTEXITCODE -ne 0) {
    Write-Error "python main.py 失败, 退出码 $LASTEXITCODE"
    exit 1
}

# 3. git 提交 (空提交不报错)
git add -A
$diff = git diff --cached --quiet
if ($LASTEXITCODE -eq 0) {
    Write-Host "无变更, 跳过提交"
    exit 0
}
git commit -m "auto: daily dashboard update $(Get-Date -Format 'yyyy-MM-dd')"

# 4. 拉取 + 推送 (.gitattributes 已设置 merge=ours, 不会冲突)
git pull --rebase origin main
git push origin main

Write-Host "[done] $(Get-Date -Format 'yyyy-MM-dd HH:mm')"
