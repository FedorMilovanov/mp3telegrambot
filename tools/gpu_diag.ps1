# GPU / CUDA / NVENC diagnostics for Windows.
# Диагностика зависаний GPU при нарезке Shorts. ТОЛЬКО ЧТЕНИЕ: скрипт лишь
# читает состояние карты и журнал событий Windows, ничего не меняет.
#
# Запуск в PowerShell из папки бота:
#     powershell -ExecutionPolicy Bypass -File tools\gpu_diag.ps1
# Результат целиком скопируй и пришли мне.

$ErrorActionPreference = 'Continue'

function Section($t) {
    Write-Host ""
    Write-Host ("=" * 72) -ForegroundColor Cyan
    Write-Host $t -ForegroundColor Cyan
    Write-Host ("=" * 72) -ForegroundColor Cyan
}

function ShowEvents($events, $emptyMsg) {
    if ($events) {
        $events | Format-List
    } else {
        Write-Host $emptyMsg -ForegroundColor Green
    }
}

$since = (Get-Date).AddDays(-14)   # окно поиска по журналу: последние 14 дней

Section "1. nvidia-smi (overview: driver, GPU, running processes)"
nvidia-smi

Section "2. GPU health: temperature / power / clocks / throttle / ECC"
nvidia-smi -q -d TEMPERATURE,POWER,CLOCK,PERFORMANCE,ECC 2>&1 |
    Select-String -Pattern "GPU Current Temp|GPU Shutdown Temp|GPU Slowdown Temp|Power Draw|Current Power Limit|SM Clock|HW Slowdown|HW Thermal Slowdown|SW Thermal Slowdown|SW Power Cap|Sync Boost|Pending|Single Bit|Double Bit|Aggregate"

Section "3. Xid errors (hardware/driver faults the GPU itself reported)"
$xid = nvidia-smi -q 2>&1 | Select-String -Pattern "Xid"
if ($xid) {
    Write-Host "!!! Xid found — это признак сбоя железа/драйвера GPU:" -ForegroundColor Red
    $xid
} else {
    Write-Host "Xid не встречается в nvidia-smi -q (хороший знак)." -ForegroundColor Green
}

Section "4. NVENC encoder / decoder utilization snapshot"
nvidia-smi -q -d UTILIZATION 2>&1 |
    Select-String -Pattern "Utilization|Gpu|Memory|Encoder|Decoder"

Section "5. TDR: Event 4101 — 'display driver stopped responding and recovered'"
$e = Get-WinEvent -FilterHashtable @{LogName='System'; Id=4101; StartTime=$since} -ErrorAction SilentlyContinue |
    Select-Object TimeCreated, Id, Message
ShowEvents $e "Событий 4101 (TDR-восстановление драйвера) за 14 дней нет — хорошо."

Section "6. NVIDIA kernel driver events (nvlddmkm) — engine hangs / GPU faults"
$e = Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='nvlddmkm'; StartTime=$since} -ErrorAction SilentlyContinue |
    Select-Object TimeCreated, Id, LevelDisplayName, Message
ShowEvents $e "Событий провайдера nvlddmkm за 14 дней нет — хорошо."

Section "7. Event ID 153 — что это ИМЕННО на твоей системе (диск или GPU?)"
$e = Get-WinEvent -FilterHashtable @{LogName='System'; Id=153; StartTime=$since} -ErrorAction SilentlyContinue |
    Select-Object TimeCreated, ProviderName, Message -First 15
ShowEvents $e "Событий с Id=153 за 14 дней нет."
Write-Host "(ProviderName подскажет источник: 'disk'/'storahci' = диск, иначе — смотри текст)" -ForegroundColor DarkGray

Section "8. WHEA hardware errors (PCIe / GPU / CPU / RAM — corrected & fatal)"
$e = Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-WHEA-Logger'; StartTime=$since} -ErrorAction SilentlyContinue |
    Select-Object TimeCreated, Id, LevelDisplayName, Message
ShowEvents $e "Событий WHEA-Logger за 14 дней нет — аппаратных ошибок шины/GPU не зафиксировано."

Section "9. Bugchecks / BSOD (Event 1001) и внезапные перезагрузки (Kernel-Power 41)"
$e = Get-WinEvent -FilterHashtable @{LogName='System'; Id=1001; StartTime=$since} -ErrorAction SilentlyContinue |
    Where-Object { $_.ProviderName -match 'BugCheck|WER' } |
    Select-Object TimeCreated, Message
ShowEvents $e "BSOD-событий (BugCheck 1001) за 14 дней нет."
$e = Get-WinEvent -FilterHashtable @{LogName='System'; Id=41; StartTime=$since} -ErrorAction SilentlyContinue |
    Select-Object TimeCreated, Message
ShowEvents $e "Событий Kernel-Power 41 (жёсткое выключение/зависание) за 14 дней нет."

Section "10. TDR registry settings (read-only)"
$gd = 'HKLM:\SYSTEM\CurrentControlSet\Control\GraphicsDrivers'
$reg = Get-ItemProperty -Path $gd -ErrorAction SilentlyContinue |
    Select-Object TdrLevel, TdrDelay, TdrDdiDelay
if ($reg) { $reg | Format-List } else { Write-Host "Ключи Tdr* не заданы — используются значения по умолчанию (TdrDelay=2 сек)." -ForegroundColor DarkGray }

Section "ИТОГ"
Write-Host "Пришли весь вывод. Что искать:"
Write-Host "  - Xid / nvlddmkm / 4101  -> драйвер или карта роняются под нагрузкой (TDR)."
Write-Host "  - WHEA fatal             -> аппаратная проблема (PCIe/питание/сам GPU)."
Write-Host "  - Temp близко к Shutdown -> перегрев, чистка/термопаста/обороты кулера."
Write-Host "  - HW/SW Slowdown = Active-> троттлинг (питание или температура)."
Write-Host "  - 153 с провайдером disk -> это про диск, к зависанию GPU отношения нет."
