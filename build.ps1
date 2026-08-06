# Hemlock 构建脚本
# 用法: powershell -ExecutionPolicy Bypass -File build.ps1
#
# 构建步骤:
# 1. 检查环境依赖
# 2. 构建前端 (React + Vite)
# 3. 打包后端 (PyInstaller)
# 4. 复制后端到 Tauri resources
# 5. 构建 Tauri 应用

param(
    [switch]$SkipBackend,
    [switch]$SkipFrontend,
    [switch]$Debug
)

$ErrorActionPreference = "Stop"
$ROOT = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "`n===== Hemlock Build Script =====" -ForegroundColor Cyan
Write-Host "Root: $ROOT`n"

# ===== Step 0: 环境检查 =====
Write-Host "[0/5] Checking environment..." -ForegroundColor Yellow

$missingDeps = @()

# 检查 Node.js
try {
    $nodeVer = node --version 2>$null
    Write-Host "  Node.js: $nodeVer" -ForegroundColor DarkGray
} catch {
    $missingDeps += "Node.js (https://nodejs.org/)"
}

# 检查 Rust
try {
    $rustVer = rustc --version 2>$null
    Write-Host "  Rust: $rustVer" -ForegroundColor DarkGray
} catch {
    $missingDeps += "Rust (https://rustup.rs/)"
}

# 检查 Python
try {
    $pythonVer = python --version 2>$null
    Write-Host "  Python: $pythonVer" -ForegroundColor DarkGray
} catch {
    $missingDeps += "Python 3.10+ (https://python.org/)"
}

if ($missingDeps.Count -gt 0) {
    Write-Host "`n缺少以下依赖:" -ForegroundColor Red
    foreach ($dep in $missingDeps) {
        Write-Host "  - $dep" -ForegroundColor Red
    }
    Write-Host ""
    exit 1
}

# 检查 .env 文件
$envFile = "$ROOT\backend\.env"
$envExample = "$ROOT\backend\.env.example"
if (-not (Test-Path $envFile)) {
    if (Test-Path $envExample) {
        Write-Host "  提示: 未找到 backend\.env，正在从 .env.example 创建..." -ForegroundColor DarkYellow
        Copy-Item $envExample $envFile
        Write-Host "  请编辑 backend\.env 配置 LLM API Key 后重新构建" -ForegroundColor DarkYellow
    }
}

Write-Host "Environment OK`n" -ForegroundColor Green

# ===== Step 1: Build Frontend =====
if (-not $SkipFrontend) {
    Write-Host "[1/5] Building frontend..." -ForegroundColor Yellow
    Push-Location "$ROOT\frontend"

    # 安装依赖
    if (-not (Test-Path "node_modules")) {
        Write-Host "  Installing npm dependencies..." -ForegroundColor DarkGray
        npm install
        if ($LASTEXITCODE -ne 0) {
            Write-Host "npm install failed!" -ForegroundColor Red
            Pop-Location
            exit 1
        }
    }

    npm run build
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Frontend build failed!" -ForegroundColor Red
        Write-Host "提示: 检查 TypeScript 编译错误" -ForegroundColor Yellow
        Pop-Location
        exit 1
    }
    Pop-Location
    Write-Host "Frontend build OK`n" -ForegroundColor Green
} else {
    Write-Host "[1/5] Skipping frontend build`n" -ForegroundColor DarkGray
}

# ===== Step 2: Package Backend with PyInstaller =====
if (-not $SkipBackend) {
    Write-Host "[2/5] Packaging backend with PyInstaller..." -ForegroundColor Yellow
    Push-Location "$ROOT\backend"

    # 激活虚拟环境
    if (Test-Path ".venv\Scripts\Activate.ps1") {
        . .venv\Scripts\Activate.ps1
    } else {
        Write-Host "  提示: 未找到虚拟环境，使用全局 Python" -ForegroundColor DarkYellow
    }

    # 安装 PyInstaller（如未安装）
    pip install pyinstaller --quiet 2>$null

    # 运行 PyInstaller
    pyinstaller hemlock-backend.spec --noconfirm
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Backend packaging failed!" -ForegroundColor Red
        Write-Host "提示: 检查 Python 依赖是否完整安装 (pip install -e .)" -ForegroundColor Yellow
        Pop-Location
        exit 1
    }
    Pop-Location
    Write-Host "Backend packaging OK`n" -ForegroundColor Green
} else {
    Write-Host "[2/5] Skipping backend packaging`n" -ForegroundColor DarkGray
}

# ===== Step 3: Copy backend to Tauri resources =====
Write-Host "[3/5] Copying backend to Tauri resources..." -ForegroundColor Yellow

$resourcesDir = "$ROOT\src-tauri\resources"
$backendDist = "$ROOT\backend\dist\hemlock-backend"

# 创建 resources 目录
if (-not (Test-Path $resourcesDir)) {
    New-Item -ItemType Directory -Path $resourcesDir -Force | Out-Null
}

# 清理旧的 backend 文件
if (Test-Path "$resourcesDir\hemlock-backend") {
    Remove-Item "$resourcesDir\hemlock-backend" -Recurse -Force
}

# 复制新的 backend 文件
if (Test-Path $backendDist) {
    Copy-Item $backendDist $resourcesDir -Recurse
    Write-Host "Backend copied to resources`n" -ForegroundColor Green
} else {
    Write-Host "Backend dist not found at $backendDist" -ForegroundColor Red
    Write-Host "Run with -SkipBackend to skip backend packaging`n" -ForegroundColor Yellow
}

# ===== Step 4: Build Tauri App =====
Write-Host "[4/5] Building Tauri application..." -ForegroundColor Yellow
Push-Location "$ROOT\src-tauri"

if ($Debug) {
    cargo tauri build --debug
} else {
    cargo tauri build
}

if ($LASTEXITCODE -ne 0) {
    Write-Host "Tauri build failed!" -ForegroundColor Red
    Write-Host "提示: 确保 cargo-tauri 已安装 (cargo install tauri-cli)" -ForegroundColor Yellow
    Pop-Location
    exit 1
}
Pop-Location
Write-Host "Tauri build OK`n" -ForegroundColor Green

# ===== Done =====
Write-Host "===== Build Complete! =====" -ForegroundColor Cyan
Write-Host "Output: src-tauri\target\release\bundle\" -ForegroundColor White
Write-Host ""

# 检查 .env 配置提醒
if (Test-Path $envFile) {
    $envContent = Get-Content $envFile -Raw
    if ($envContent -match "your-api-key-here") {
        Write-Host "警告: backend\.env 中 LLM_API_KEY 仍为默认值" -ForegroundColor Yellow
        Write-Host "  Agent 功能需要有效的 API Key 才能运行" -ForegroundColor White
    }
} else {
    Write-Host "注意: 首次运行前请确保已配置 backend\.env 文件:" -ForegroundColor Yellow
    Write-Host "  HEMLOCK_LLM_API_KEY=your-api-key" -ForegroundColor White
        Write-Host "  HEMLOCK_LLM_BASE_URL=https://api.deepseek.com/v1" -ForegroundColor White
        Write-Host "  HEMLOCK_LLM_MODEL=deepseek-v4-flash" -ForegroundColor White
}

Write-Host ""
Write-Host "开发模式运行: powershell -File dev.ps1" -ForegroundColor DarkGray
