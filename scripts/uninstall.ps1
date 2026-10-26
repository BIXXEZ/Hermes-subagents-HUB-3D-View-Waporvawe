<#
  office_hermes — удаление (Windows). Обратная операция к install.ps1.

    powershell -ExecutionPolicy Bypass -File scripts\uninstall.ps1
    powershell -ExecutionPolicy Bypass -File scripts\uninstall.ps1 -KeepFiles
#>
[CmdletBinding()]
param(
  [string]$HermesHome = $(if ($env:HERMES_HOME) { $env:HERMES_HOME } else { Join-Path $env:LOCALAPPDATA 'hermes' }),
  [string]$StartupDir = [Environment]::GetFolderPath('Startup'),
  [switch]$KeepFiles
)

$ErrorActionPreference = 'Continue'

# только свои процессы — по пути к каталогу ЭТОГО home; чужие фиды не трогаем
$liveDir = Join-Path $HermesHome 'office-live'
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
  Where-Object { $_.Name -match '^python' -and $_.CommandLine -like '*office_live.py*' -and $_.CommandLine -like "*$liveDir*" } |
  ForEach-Object { Write-Host "останавливаю фид (pid $($_.ProcessId))"; Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

# ярлык «вход» из меню Пуск
$lnk = Join-Path ([Environment]::GetFolderPath('Programs')) 'office_hermes.lnk'
if (Test-Path $lnk) { Remove-Item $lnk -Force -ErrorAction SilentlyContinue; Write-Host "удалён ярлык: $lnk" }

$vbs = Join-Path $StartupDir 'hermes-office.vbs'
if (Test-Path $vbs) { Remove-Item $vbs -Force; Write-Host "удалён автозапуск: $vbs" }

if ($KeepFiles) {
  Write-Host "файлы оставлены (-KeepFiles). Панель отключить: уберите $HermesHome\desktop-plugins\office-hermes"
  exit 0
}

foreach ($d in @((Join-Path $HermesHome 'desktop-plugins\office-hermes'), (Join-Path $HermesHome 'office-live'))) {
  if (Test-Path $d) { Remove-Item $d -Recurse -Force; Write-Host "удалено: $d" }
}

Write-Host ""
Write-Host "Готово. В Hermes: Ctrl+K → «Reload desktop plugins», панель исчезнет."
