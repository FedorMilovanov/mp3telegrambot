# GPU / CUDA / NVENC diagnostics for Windows.
# Read-only: this script only READS GPU state and the Windows event log.
# It changes nothing. ASCII-only so it runs on Windows PowerShell 5.1 and 7+.
#
# Run from the bot folder:
#     powershell -ExecutionPolicy Bypass -File tools\gpu_diag.ps1
# Then copy the WHOLE output and send it back.

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

$since = (Get-Date).AddDays(-14)   # search window in the event log: last 14 days

Section "1. nvidia-smi (overview: driver, GPU, running processes)"
nvidia-smi

Section "2. GPU health: temperature / power / clocks / throttle / ECC"
nvidia-smi -q -d TEMPERATURE,POWER,CLOCK,PERFORMANCE,ECC 2>&1 |
    Select-String -Pattern "GPU Current Temp|GPU Shutdown Temp|GPU Slowdown Temp|Power Draw|Current Power Limit|SM Clock|HW Slowdown|HW Thermal Slowdown|SW Thermal Slowdown|SW Power Cap|Sync Boost|Pending|Single Bit|Double Bit|Aggregate"

Section "3. Xid errors (hardware/driver faults reported by the GPU itself)"
$xid = nvidia-smi -q 2>&1 | Select-String -Pattern "Xid"
if ($xid) {
    Write-Host "!!! Xid found - a sign of a GPU hardware/driver fault:" -ForegroundColor Red
    $xid
} else {
    Write-Host "No Xid lines in nvidia-smi -q (good sign)." -ForegroundColor Green
}

Section "4. NVENC encoder / decoder utilization snapshot"
nvidia-smi -q -d UTILIZATION 2>&1 |
    Select-String -Pattern "Utilization|Gpu|Memory|Encoder|Decoder"

Section "5. TDR: Event 4101 - 'display driver stopped responding and recovered'"
$e = Get-WinEvent -FilterHashtable @{LogName='System'; Id=4101; StartTime=$since} -ErrorAction SilentlyContinue |
    Select-Object TimeCreated, Id, Message
ShowEvents $e "No 4101 (TDR driver-recovery) events in the last 14 days - good."

Section "6. NVIDIA kernel driver events (nvlddmkm) - engine hangs / GPU faults"
$e = Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='nvlddmkm'; StartTime=$since} -ErrorAction SilentlyContinue |
    Select-Object TimeCreated, Id, LevelDisplayName, Message
ShowEvents $e "No nvlddmkm provider events in the last 14 days - good."

Section "7. Event ID 153 - what it actually IS on your system (disk or GPU?)"
$e = Get-WinEvent -FilterHashtable @{LogName='System'; Id=153; StartTime=$since} -ErrorAction SilentlyContinue |
    Select-Object TimeCreated, ProviderName, Message -First 15
ShowEvents $e "No Id=153 events in the last 14 days."
Write-Host "(ProviderName tells the source: 'disk'/'storahci' = disk, otherwise read the text)" -ForegroundColor DarkGray

Section "8. WHEA hardware errors (PCIe / GPU / CPU / RAM - corrected and fatal)"
$e = Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-WHEA-Logger'; StartTime=$since} -ErrorAction SilentlyContinue |
    Select-Object TimeCreated, Id, LevelDisplayName, Message
ShowEvents $e "No WHEA-Logger events in the last 14 days - no hardware bus/GPU errors recorded."

Section "9. Bugchecks / BSOD (Event 1001) and hard reboots (Kernel-Power 41)"
$e = Get-WinEvent -FilterHashtable @{LogName='System'; Id=1001; StartTime=$since} -ErrorAction SilentlyContinue |
    Where-Object { $_.ProviderName -match 'BugCheck|WER' } |
    Select-Object TimeCreated, Message
ShowEvents $e "No BSOD (BugCheck 1001) events in the last 14 days."
$e = Get-WinEvent -FilterHashtable @{LogName='System'; Id=41; StartTime=$since} -ErrorAction SilentlyContinue |
    Select-Object TimeCreated, Message
ShowEvents $e "No Kernel-Power 41 (hard shutdown/freeze) events in the last 14 days."

Section "10. TDR registry settings (read-only)"
$gd = 'HKLM:\SYSTEM\CurrentControlSet\Control\GraphicsDrivers'
$reg = Get-ItemProperty -Path $gd -ErrorAction SilentlyContinue |
    Select-Object TdrLevel, TdrDelay, TdrDdiDelay
if ($reg) { $reg | Format-List } else { Write-Host "No Tdr* keys set - defaults in use (TdrDelay=2 sec)." -ForegroundColor DarkGray }

Section "SUMMARY - what to look for"
Write-Host "Send the whole output. Interpretation:"
Write-Host "  - Xid / nvlddmkm / 4101   -> driver or card crashing under load (TDR)."
Write-Host "  - WHEA fatal              -> hardware problem (PCIe / power / GPU itself)."
Write-Host "  - Temp near Shutdown      -> overheating: dust/thermal paste/fan RPM."
Write-Host "  - HW/SW Slowdown = Active -> throttling (power or temperature)."
Write-Host "  - 153 with provider disk  -> that's about the DISK, unrelated to GPU hangs."
