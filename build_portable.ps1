# Build the portable ("green") package of PDFTextEditor.
# Usage:  powershell -ExecutionPolicy Bypass -File build_portable.ps1 [-SkipBuild]
#
# Steps: build onedir -> stage under worktemp\portable -> add portable.flag and
# 使用说明.txt -> zip. Nothing is written outside the project folder.
#
# -SkipBuild reuses the existing worktemp\pyinstaller\dist output (fast re-zip).
#
# Output: worktemp\portable\PDFTextEditor-Portable-<VERSION>.zip
param(
    [string]$Name = "PDFTextEditor",
    [switch]$SkipBuild       # reuse the existing worktemp\pyinstaller\dist output
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

# --- version comes from the single source VERSION ---
$versionFile = Join-Path $PSScriptRoot "VERSION"
if (-not (Test-Path $versionFile)) { Write-Error "缺少 VERSION 文件（内容形如 2.0.0）" }
$version = (Get-Content $versionFile -Raw).Trim()

$docSrc  = Join-Path $PSScriptRoot "packaging\使用说明.txt"
if (-not (Test-Path $docSrc)) { Write-Error "缺少说明文件：$docSrc" }

$distDir = Join-Path $PSScriptRoot "worktemp\pyinstaller\dist\$Name"
$stageRoot = Join-Path $PSScriptRoot "worktemp\portable"
$stage   = Join-Path $stageRoot $Name
$zipPath = Join-Path $stageRoot "$Name-Portable-$version.zip"

# --- 1) build（-SkipBuild 时复用上一次的 onedir 输出）---
if ($SkipBuild) {
    Write-Host "==> skipping build (reusing $distDir)"
} else {
    & (Join-Path $PSScriptRoot "build_exe.ps1") -Name $Name
}
if (-not (Test-Path (Join-Path $distDir "$Name.exe"))) {
    Write-Error "找不到 $distDir\$Name.exe（先不带 -SkipBuild 跑一次）"
}

# --- 2) stage ---
Write-Host "==> staging ..."
if (Test-Path $stageRoot) { Remove-Item -Recurse -Force $stageRoot }
New-Item -ItemType Directory -Path $stageRoot -Force | Out-Null
Copy-Item -Recurse -Force $distDir $stage

# --- 3) portable marker (makes the app keep its cache inside the folder) ---
New-Item -ItemType File -Path (Join-Path $stage "portable.flag") -Force | Out-Null

# --- 4) readme next to the exe (UTF-8 without BOM) ---
$doc = (Get-Content $docSrc -Raw -Encoding UTF8).Replace("@VERSION@", $version)
[System.IO.File]::WriteAllText((Join-Path $stage "使用说明.txt"), $doc, (New-Object System.Text.UTF8Encoding($false)))

# --- 5) zip ---
Write-Host "==> compressing ..."
if (Test-Path $zipPath) { Remove-Item -Force $zipPath }
Compress-Archive -Path $stage -DestinationPath $zipPath -CompressionLevel Optimal

# --- 6) report ---
$size = [math]::Round((Get-Item $zipPath).Length / 1MB, 1)
$hash = (Get-FileHash $zipPath -Algorithm SHA256).Hash
Write-Host ""
Write-Host "done."
Write-Host "  zip    : $zipPath"
Write-Host "  size   : $size MB"
Write-Host "  sha256 : $hash"
