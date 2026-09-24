# 打包 PDFTextEditor 为免安装单文件 exe
# 用法： powershell -ExecutionPolicy Bypass -File build_exe.ps1
param(
    [string]$Name = "PDFTextEditor"
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

Write-Host "==> 检查 PyInstaller ..."
python -m PyInstaller --version *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Error "未安装 PyInstaller，请先执行: pip install pyinstaller"
}

Write-Host "==> 清理旧产物 ..."
Remove-Item -Recurse -Force "build", "dist" -ErrorAction SilentlyContinue
Remove-Item -Force "$Name.spec" -ErrorAction SilentlyContinue

Write-Host "==> 开始打包 ..."
python -m PyInstaller `
    --noconfirm --clean --onefile --windowed `
    --name $Name `
    --hidden-import fitz `
    --hidden-import fontTools `
    --hidden-import fontTools.ttLib `
    --hidden-import numpy `
    --collect-all pymupdf `
    pdf_editor_gui.py

Write-Host ""
Write-Host "完成。产物：$PSScriptRoot\dist\$Name.exe"
