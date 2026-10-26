<#
  office_hermes — установка в Hermes (Windows).

  Что делает:
    1. находит pythonw.exe;
    2. копирует фид в  <HermesHome>\office-live\  (office_live.py + office.html);
    3. копирует панель в <HermesHome>\desktop-plugins\office-hermes\plugin.js
       (с подстановкой порта);
    4. кладёт автозапуск hermes-office.vbs в Startup;
    5. останавливает прошлый фид, запускает новый и ждёт /health.

  Примеры:
    powershell -ExecutionPolicy Bypass -File scripts\install.ps1
    powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -Port 8801 -NoAutostart
    powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -HermesHome D:\hermes-test

  После установки: в Hermes нажать Ctrl+K → «Reload desktop plugins».
#>
[CmdletBinding()]
param(
  [string]$HermesHome = $(if ($env:HERMES_HOME) { $env:HERMES_HOME } else { Join-Path $env:LOCALAPPDATA 'hermes' }),
  [int]$Port = $(if ($env:OFFICE_PORT) { [int]$env:OFFICE_PORT } else { 8797 }),
  [string]$PythonExe,
  [string]$StartupDir = [Environment]::GetFolderPath('Startup'),
  [switch]$NoAutostart,
  [switch]$NoStart,
  [switch]$Open,
  [switch]$Quiet
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path (Join-Path $repo 'plugin.js'))) { $repo = $PSScriptRoot }

function Say($m) { if (-not $Quiet) { Write-Host $m } }
function Fail($m) { Write-Host "ОШИБКА: $m" -ForegroundColor Red; exit 1 }

Say "office_hermes → установка"
Say "  репозиторий : $repo"
Say "  Hermes home : $HermesHome"
Say "  порт        : $Port"

# --- 1. проверка источника
foreach ($f in @('plugin.js', 'server\office_live.py', 'web\office.html')) {
  if (-not (Test-Path (Join-Path $repo $f))) { Fail "в комплекте нет $f" }
}
if (-not (Test-Path $HermesHome)) {
  Write-Host "  ! каталог Hermes не найден: $HermesHome" -ForegroundColor Yellow
  Write-Host "    Сервер и панель установятся, но state.db появится только после запуска Hermes."
}

# --- 2. pythonw
function Resolve-Pythonw {
  param([string]$Explicit)
  if ($Explicit) {
    if (Test-Path $Explicit) { return (Resolve-Path $Explicit).Path }
    Fail "не найден python: $Explicit"
  }
  $cands = @()
  $cmd = Get-Command pythonw.exe -ErrorAction SilentlyContinue
  if ($cmd) { $cands += $cmd.Source }
  $cands += Get-ChildItem -Path (Join-Path $HermesHome 'tools') -Filter 'pythonw.exe' -Recurse -ErrorAction SilentlyContinue |
            Sort-Object FullName -Descending | Select-Object -ExpandProperty FullName -First 3
  $py = Get-Command python.exe -ErrorAction SilentlyContinue
  if ($py) { $cands += (Join-Path (Split-Path $py.Source) 'pythonw.exe') }
  foreach ($c in $cands) { if ($c -and (Test-Path $c)) { return (Resolve-Path $c).Path } }
  return $null
}
$pyw = Resolve-Pythonw -Explicit $PythonExe
if (-not $pyw) {
  Fail "не нашёл pythonw.exe. Укажите путь вручную: -PythonExe 'C:\Python312\pythonw.exe'"
}
Say "  python      : $pyw"

# --- 3. файлы
$liveDir = Join-Path $HermesHome 'office-live'
$plugDir = Join-Path $HermesHome 'desktop-plugins\office-hermes'
New-Item -ItemType Directory -Force -Path $liveDir, $plugDir | Out-Null

Copy-Item (Join-Path $repo 'server\office_live.py') (Join-Path $liveDir 'office_live.py') -Force
Copy-Item (Join-Path $repo 'web\office.html')       (Join-Path $liveDir 'office.html')    -Force
Copy-Item (Join-Path $repo 'README.md')             (Join-Path $liveDir 'README.md')      -Force -ErrorAction SilentlyContinue

# plugin.js с нужным портом
$plugSrc = Get-Content (Join-Path $repo 'plugin.js') -Raw
$plugOut = $plugSrc -replace "http://127\.0\.0\.1:\d+", "http://127.0.0.1:$Port"
$plugDest = Join-Path $plugDir 'plugin.js'
if ((Test-Path $plugDest) -and ((Get-Content $plugDest -Raw) -ne $plugOut)) {
  Copy-Item $plugDest "$plugDest.bak" -Force
}
$utf8 = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($plugDest, $plugOut, $utf8)
Say "  панель      : $plugDir\plugin.js"
Say "  фид         : $liveDir\office_live.py"

# --- 4. лаунчер (тихий запуск без консоли) + автозапуск
# wscript отвязывает pythonw от нашего stdout: иначе `install.ps1 | tail` висит,
# пока жив фид (пайп держит унаследованный handle).
$runVbs = Join-Path $liveDir 'start-office.vbs'
$logFile = Join-Path $liveDir 'feed.log'
$cmdLine = '"' + $pyw + '" "' + (Join-Path $liveDir 'office_live.py') + '" --port ' + $Port + ' --home "' + $HermesHome + '" --log "' + $logFile + '"'
$vbsBody = "Set sh = CreateObject(""WScript.Shell"")" + "`r`n" +
           'sh.Run "' + ($cmdLine -replace '"', '""') + '", 0, False' + "`r`n"
[System.IO.File]::WriteAllText($runVbs, $vbsBody, [System.Text.Encoding]::ASCII)
Say "  лаунчер     : $runVbs"

$vbs = Join-Path $StartupDir 'hermes-office.vbs'
if ($NoAutostart) {
  Say "  автозапуск  : пропущен (-NoAutostart)"
} else {
  if (-not (Test-Path $StartupDir)) { New-Item -ItemType Directory -Force -Path $StartupDir | Out-Null }
  Copy-Item $runVbs $vbs -Force
  Say "  автозапуск  : $vbs"
}

# --- 5. перезапуск фида
# только свои процессы — по пути к этому каталогу, чужие фиды не трогаем
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -like "*office_live.py*" -and $_.CommandLine -like "*$liveDir*" } |
  ForEach-Object {
    Say "  останавливаю старый фид (pid $($_.ProcessId))"
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
  }
Start-Sleep -Milliseconds 400

if ($NoStart) {
  Say "  запуск      : пропущен (-NoStart)"
} else {
  # без -Wait: он держит вложенный pythonw, и `install.ps1 | tail` не закрывается
  Start-Process -FilePath "$env:WINDIR\system32\wscript.exe" -ArgumentList ('"' + $runVbs + '"') -WindowStyle Hidden
  Start-Sleep -Milliseconds 300
  $health = $null
  $foreign = $false
  for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Milliseconds 350
    try {
      $h = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/health" -UseBasicParsing -TimeoutSec 2
      $j = $h.Content | ConvertFrom-Json
      if ($j.service -eq 'office_hermes') { $health = $j; break }
      $foreign = $true; break            # на порту кто-то чужой
    } catch { }
  }
  if ($foreign) {
    Fail "порт $Port занят другим сервисом (не office_hermes). Возьмите другой: -Port 8801"
  }
  if (-not $health) {
    $tail = if (Test-Path $logFile) { (Get-Content $logFile -Tail 15) -join "`n" } else { '(нет вывода)' }
    Fail "фид не поднялся на 127.0.0.1:$Port.`n$tail`nЗапустите вручную: `"$pyw`" `"$liveDir\office_live.py`" --port $Port --home `"$HermesHome`""
  }
  Say "  фид         : OK  http://127.0.0.1:$Port/  (state.db: $(if ($health.db) { 'найден' } else { 'НЕТ' }))"
  if (-not $health.db) {
    Write-Host "  ! state.db не найден — откройте Hermes хотя бы раз, потом перезапустите фид." -ForegroundColor Yellow
  }
  if ($Open) { Start-Process "http://127.0.0.1:$Port/" }
}

Say ""
Say "Готово. Дальше в Hermes:"
Say "  1) Ctrl+K → «Reload desktop plugins»"
Say "  2) справа появится панель office_hermes (или чип «office_hermes» в статусбаре)"
Say "Адрес офиса в браузере: http://127.0.0.1:$Port/"
if (-not $NoAutostart) { Say "Автозапуск при входе в Windows: $vbs" }
