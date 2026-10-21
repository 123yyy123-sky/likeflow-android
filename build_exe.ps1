$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

python -m pip install -r requirements-dev.txt

python -m PyInstaller --noconfirm --clean LikeFlow.spec

Write-Host ""
Write-Host "Built: $PSScriptRoot\dist\LikeFlow.exe" -ForegroundColor Green
