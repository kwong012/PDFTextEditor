# Build PDFTextEditor into a single portable exe.
# Usage:  powershell -ExecutionPolicy Bypass -File build_exe.ps1
#
# Prefers the project-local virtual environment .venv; falls back to global python.
# Recommended setup:
#   python -m venv .venv
#   .\.venv\Scripts\python.exe -m pip install -r requirements.txt pyinstaller
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
    Write-Host "==> no .venv found, using global python (create .venv for isolation)"
}

Write-Host "==> checking PyInstaller ..."
& $Py -m PyInstaller --version *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Error "PyInstaller not available. Run: `"$Py`" -m pip install -r requirements.txt pyinstaller"
}

Write-Host "==> cleaning previous output ..."
Remove-Item -Recurse -Force "build", "dist" -ErrorAction SilentlyContinue
Remove-Item -Force "$Name.spec" -ErrorAction SilentlyContinue

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
    --icon icon.ico `
    --add-data "icon.ico;." `
    pdf_editor_gui.py

Write-Host ""
Write-Host "done. output: $PSScriptRoot\dist\$Name.exe"
