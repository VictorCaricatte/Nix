# Gera o instalador do Nix:  powershell -ExecutionPolicy Bypass -File installer\build_installer.ps1
#   1) PyInstaller (modo pasta)  -> dist_installer\Nix\
#   2) Inno Setup                -> installer_output\Nix_Setup_<versao>.exe
#   3) ZIP portátil              -> installer_output\Nix_Portable_<versao>.zip
# Opções: -Version 1.3.0   -Python <python.exe com as dependências>   -SkipBuild (reusa dist_installer)
param([string]$Version = "1.3.0", [string]$Python = "python", [switch]$SkipBuild)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location (Join-Path $root "Nix")

if (-not $SkipBuild) {
    Write-Host "==> Verificando dependencias ($Python)" -ForegroundColor Cyan
    & $Python -c "import PyInstaller, PyQt6, paramiko, pyte, qtawesome"
    if ($LASTEXITCODE -ne 0) { throw "Faltam dependencias neste Python. Rode:  $Python -m pip install -r requirements.txt pyinstaller  (ou use -Python <caminho do python.exe>)" }

    Write-Host "==> PyInstaller" -ForegroundColor Cyan
    & $Python -m PyInstaller --noconfirm --clean --distpath "$root\dist_installer" --workpath "$root\build_installer" Nix.spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller falhou" }
}
if (-not (Test-Path "$root\dist_installer\Nix\Nix.exe")) { throw "dist_installer\Nix\Nix.exe nao existe (rode sem -SkipBuild)" }

New-Item -ItemType Directory -Force "$root\installer_output" | Out-Null

$candidates = @(
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
)
$iscc = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($iscc) {
    Write-Host "==> Inno Setup" -ForegroundColor Cyan
    & $iscc "/DMyAppVersion=$Version" "$root\installer\Nix.iss"
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup falhou" }
} else {
    Write-Warning "Inno Setup nao encontrado. Instale com:  winget install JRSoftware.InnoSetup  e rode de novo."
}

# ZIP portátil (o antivírus às vezes trava arquivos recém-criados: tenta de novo)
Write-Host "==> ZIP portatil" -ForegroundColor Cyan
$zip = "$root\installer_output\Nix_Portable_$Version.zip"
for ($i = 1; $i -le 5; $i++) {
    try {
        if (Test-Path $zip) { Remove-Item $zip -Force }
        Compress-Archive -Path "$root\dist_installer\Nix" -DestinationPath $zip -ErrorAction Stop
        break
    } catch {
        if ($i -eq 5) { Write-Warning "Nao foi possivel gerar o ZIP portatil: $($_.Exception.Message)" }
        else { Start-Sleep -Seconds 5 }
    }
}
Write-Host "Pronto: $root\installer_output" -ForegroundColor Green
