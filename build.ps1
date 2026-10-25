# Gera dist\ChicoteDigital.exe.  Uso:  powershell -ExecutionPolicy Bypass -File build.ps1
# Requisitos (uma vez):  python -m pip install --user pyqt6 pyinstaller pillow
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

python gerar_icone.py
if ($LASTEXITCODE -ne 0) { throw "falha ao gerar o ícone" }

# arquivos intermediários vão para a pasta temporária, fora do OneDrive
$temp = Join-Path $env:TEMP "ChicoteDigital-build"
python -m PyInstaller ChicoteDigital.spec --noconfirm --clean --workpath $temp --distpath dist
if ($LASTEXITCODE -ne 0) { throw "falha no PyInstaller" }

Get-Item dist\ChicoteDigital.exe | Select-Object Name, @{n="MB"; e={[math]::Round($_.Length / 1MB, 1)}}
