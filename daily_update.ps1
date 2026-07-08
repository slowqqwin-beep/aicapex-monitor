# AI Capex 二阶导看板 · 本地每日自动更新
# 用法: powershell -ExecutionPolicy Bypass -File daily_update.ps1
$ErrorActionPreference = "Stop"

# 1. 彻底删代理 (定时任务可能继承系统级代理)
Remove-Item Env:HTTP_PROXY -ErrorAction SilentlyContinue
Remove-Item Env:HTTPS_PROXY -ErrorAction SilentlyContinue
Remove-Item Env:http_proxy -ErrorAction SilentlyContinue
Remove-Item Env:https_proxy -ErrorAction SilentlyContinue
Remove-Item Env:ALL_PROXY -ErrorAction SilentlyContinue

# 2. 切到脚本所在目录
Set-Location $PSScriptRoot

# 3. 抓数据 + 渲染
python main.py
if ($LASTEXITCODE -ne 0) {
    Write-Error "python main.py failed, code $LASTEXITCODE"
    exit 1
}

# 4. git
git add -A
git diff --cached --quiet
if ($LASTEXITCODE -eq 0) {
    Write-Host "no changes, skip commit"
    exit 0
}
git commit -m "auto: daily dashboard update $(Get-Date -Format 'yyyy-MM-dd')"
git pull --rebase origin main
git push origin main

Write-Host "[done] $(Get-Date -Format 'yyyy-MM-dd HH:mm')"
