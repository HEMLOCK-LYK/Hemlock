# Hemlock 开发环境启动脚本
# 用法: powershell -ExecutionPolicy Bypass -File dev.ps1

$ErrorActionPreference = "Stop"
$ROOT = Split-Path -Parent $MyInvocation.MyCommand.Definition

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Hemlock Development Environment" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# 确保 cargo 在 PATH 中
$cargoBin = Join-Path $env:USERPROFILE ".cargo\bin"
if (Test-Path $cargoBin) {
    $env:PATH = "$cargoBin;$env:PATH"
}

# 1. 检查 Python 虚拟环境
$venvPython = Join-Path $ROOT "backend\.venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "[ERROR] Python venv not found." -ForegroundColor Red
    exit 1
}

# 2. 检查 Node modules
$nodeModules = Join-Path $ROOT "frontend\node_modules"
if (-not (Test-Path $nodeModules)) {
    Write-Host "[ERROR] node_modules not found. Run: cd frontend; npm install" -ForegroundColor Red
    exit 1
}

Write-Host "[OK] Environment checks passed" -ForegroundColor Green

# 3. 启动后端
Write-Host "Starting backend (FastAPI on :9876)..." -ForegroundColor Yellow
$backendJob = Start-Job -ScriptBlock {
    param($python, $root)
    Set-Location (Join-Path $root "backend")
    & $python -m uvicorn hemlock.main:app --host 127.0.0.1 --port 9876 --reload
} -ArgumentList $venvPython, $ROOT

# 4. 启动前端
Write-Host "Starting frontend (Vite on :5173)..." -ForegroundColor Yellow
$frontendJob = Start-Job -ScriptBlock {
    param($root)
    Set-Location (Join-Path $root "frontend")
    npx vite --host 127.0.0.1 --port 5173
} -ArgumentList $ROOT

# 5. 等待服务就绪
Start-Sleep -Seconds 4
try {
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:9876/health" -TimeoutSec 5
    Write-Host "[OK] Backend: $($health.status)" -ForegroundColor Green
} catch {
    Write-Host "[WARN] Backend still starting..." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Backend API:  http://127.0.0.1:9876" -ForegroundColor White
Write-Host "  API Docs:     http://127.0.0.1:9876/docs" -ForegroundColor White
Write-Host "  Frontend:     http://127.0.0.1:5173" -ForegroundColor White
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Press Ctrl+C to stop all services." -ForegroundColor Yellow

# 6. 实时显示日志
try {
    while ($true) {
        Receive-Job $backendJob -ErrorAction Continue | ForEach-Object { Write-Host "[BE] $_" -ForegroundColor DarkGray }
        Receive-Job $frontendJob -ErrorAction Continue | ForEach-Object { Write-Host "[FE] $_" -ForegroundColor DarkGray }
        Start-Sleep -Milliseconds 500
    }
} finally {
    Stop-Job $backendJob -ErrorAction SilentlyContinue
    Stop-Job $frontendJob -ErrorAction SilentlyContinue
    Remove-Job $backendJob -ErrorAction SilentlyContinue
    Remove-Job $frontendJob -ErrorAction SilentlyContinue
    Write-Host "All services stopped." -ForegroundColor Green
}
