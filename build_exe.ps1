# Build PDFTextEditor into a single portable exe.
# Usage:  powershell -ExecutionPolicy Bypass -File build_exe.ps1 [-Console]
#
# Interpreter: prefers the project-local venv ".venv"; falls back to global python.
#   python -m venv .venv
#   .\.venv\Scripts\python.exe -m pip install -r requirements.txt pyinstaller
#
# Build intermediates AND the exe are written under "worktemp\pyinstaller"
# (which is git-ignored), so the project root stays clean.
param(
    [string]$Name = "PDFTextEditor",
    [switch]$Console          # keep a console window (useful to see errors)
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

# --- pick interpreter ---
$Py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (Test-Path $Py) {
    Write-Host "==> using venv: $Py"
} else {
    $Py = "python"
    Write-Host "==> no .venv found, using global python (see README to create one)"
}

Write-Host "==> checking PyInstaller ..."
& $Py -m PyInstaller --version *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Error "PyInstaller not available. Run: `"$Py`" -m pip install -r requirements.txt pyinstaller"
}

# --- output layout: everything under worktemp\pyinstaller ---
$outBase = Join-Path $PSScriptRoot "worktemp\pyinstaller"
$icon    = Join-Path $PSScriptRoot "assets\icon.ico"

Write-Host "==> cleaning previous output ..."
Remove-Item -Recurse -Force (Join-Path $outBase "build"), (Join-Path $outBase "dist") -ErrorAction SilentlyContinue
Remove-Item -Force (Join-Path $outBase "$Name.spec") -ErrorAction SilentlyContinue

$mode = if ($Console) { "--console" } else { "--windowed" }
Write-Host "==> building ($mode) ..."
& $Py -m PyInstaller `
    --noconfirm --clean --onefile $mode `
    --name $Name `
    --hidden-import fitz `
    --hidden-import fontTools `
    --hidden-import fontTools.ttLib `
    --hidden-import numpy `
    --collect-all pymupdf `
    --icon "$icon" `
    --add-data "$icon;assets" `
    --distpath "$outBase\dist" `
    --workpath "$outBase\build" `
    --specpath "$outBase" `
    pdf_editor_gui.py

Write-Host ""
Write-Host "done. output: $outBase\dist\$Name.exe"
