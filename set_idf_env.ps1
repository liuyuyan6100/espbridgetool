# ESP-IDF 环境统一加载脚本
# 用法: . .\set_idf_env.ps1 [项目目录]
# 不传参数则用默认项目目录
#
# 此脚本会:
# 1. 读取项目 CMakeCache.txt 中已记录的 Python 环境（如果有）
# 2. 设置 IDF_PYTHON_ENV_PATH 指向该环境
# 3. 加载 ESP-IDF 的 export.ps1
# 4. 验证当前激活的 Python 和 CMakeCache 一致
#
# 解决问题: 系统上存在两个 Python 3.13.3 虚拟环境
#   - C:\Espressif\python_env\idf5.5_py3.13_env  (espbridgetool 使用)
#   - C:\Espressif\tools\python\v5.5.4\venv       (export.ps1 默认)
# 两者路径不同但版本相同，交叉使用会导致 idf.py 报 Python 环境不匹配错误。

param(
    [string]$ProjectDir = "D:\code\espclaw\esp-claw\application\edge_agent",
    [string]$ExportScript = "C:\esp\v5.5.4\esp-idf\export.ps1"
)

# 1. 读取 CMakeCache 中的 Python 路径
$cachePath = Join-Path $ProjectDir "build\CMakeCache.txt"
$pyEnvPath = $null

if (Test-Path $cachePath) {
    $line = Select-String -Path $cachePath -Pattern '_Python3_EXECUTABLE:INTERNAL=(.+python\.exe)' | Select-Object -First 1
    if ($line) {
        $pyExe = $line.Matches[0].Groups[1].Value.Trim() -replace '/', '\'
        # 从 python.exe 路径提取环境根目录
        # C:\Espressif\...\Scripts\python.exe -> C:\Espressif\...
        $parts = $pyExe -split '\\'
        if ($parts.Length -ge 3 -and $parts[-1] -eq 'python.exe' -and $parts[-2] -eq 'Scripts') {
            $pyEnvPath = ($parts[0..($parts.Length - 3)] -join '\')
        }
    }
}

# 2. 如果没有 CMakeCache，扫描 python_env 目录
if (-not $pyEnvPath) {
    Write-Host "[set_idf_env] CMakeCache 不存在或未找到 Python 路径，扫描 python_env 目录..." -ForegroundColor Yellow
    $pyEnvBase = "C:\Espressif\python_env"
    if (Test-Path $pyEnvBase) {
        $envs = Get-ChildItem $pyEnvBase -Directory | Where-Object {
            $_.Name -match '^idf\d+\.\d+_py\d+\.\d+_env$' -and
            (Test-Path (Join-Path $_.FullName "Scripts\python.exe"))
        } | Sort-Object Name -Descending
        if ($envs) {
            $pyEnvPath = $envs[0].FullName
        }
    }
}

if ($pyEnvPath -and (Test-Path $pyEnvPath)) {
    $env:IDF_PYTHON_ENV_PATH = $pyEnvPath
    Write-Host "[set_idf_env] IDF_PYTHON_ENV_PATH = $pyEnvPath" -ForegroundColor Green
} else {
    Write-Host "[set_idf_env] 警告: 未找到 Python 环境，export.ps1 将使用默认环境" -ForegroundColor Yellow
}

# 3. 加载 export.ps1
if (Test-Path $ExportScript) {
    Write-Host "[set_idf_env] 加载 $ExportScript ..." -ForegroundColor Cyan
    . $ExportScript
    if ($?) {
        Write-Host "[set_idf_env] ESP-IDF 环境加载成功" -ForegroundColor Green
    } else {
        Write-Host "[set_idf_env] ESP-IDF 环境加载失败" -ForegroundColor Red
    }
} else {
    Write-Host "[set_idf_env] 错误: export.ps1 不存在: $ExportScript" -ForegroundColor Red
    return
}

# 4. 验证当前 Python
$currentPy = (python -c "import sys; print(sys.executable)" 2>$null)
Write-Host "[set_idf_env] 当前 Python: $currentPy" -ForegroundColor Cyan

if ($pyEnvPath -and $currentPy) {
    if ($currentPy -like "*$pyEnvPath*") {
        Write-Host "[set_idf_env] 验证通过: Python 环境一致" -ForegroundColor Green
    } else {
        Write-Host "[set_idf_env] 警告: 当前 Python 与预期不一致!" -ForegroundColor Yellow
        Write-Host "  预期: $pyEnvPath" -ForegroundColor Yellow
        Write-Host "  实际: $currentPy" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "现在可以运行 idf.py 命令了，例如:" -ForegroundColor Cyan
Write-Host "  idf.py build" -ForegroundColor White
Write-Host "  idf.py -p COM6 flash" -ForegroundColor White
Write-Host ""
