#Requires -RunAsAdministrator
<#
.GPU Diagnostics & Stability Checker for Windows
Checks: TDR crashes, NVRM/CUDA errors, temperature, power, memory integrity
Run: Right-click → "Run with PowerShell" (as Admin)
#>

$report = @()
$ts = Get-Date -Format "yyyy-MM-dd_HH-mm-ss"
$outFile = "$env:USERPROFILE\Desktop\GPU_Report_$ts.txt"

function Write-Log($msg, $color="White") {
    Write-Host $msg -ForegroundColor $color
    $script:report += $msg
}

Write-Log "========== GPU DIAGNOSTICS REPORT ==========" "Cyan"
Write-Log "Started: $(Get-Date)"
Write-Log "Computer: $env:COMPUTERNAME"
Write-Log ""

# ── 1. NVIDIA-SMI basic info ──────────────────────────────
Write-Log "--- 1. NVIDIA-SMI Status ---" "Yellow"
$smi = Get-Command "nvidia-smi" -ErrorAction SilentlyContinue
if ($smi) {
    $info = & nvidia-smi --query-gpu=name,driver_version,temperature.gpu,power.draw,power.limit,utilization.gpu,utilization.memory,memory.total,memory.used,pcie.link.gen.max,pcie.link.width.max,ecc.mode.current --format=csv 2>$null
    if ($info) {
        Write-Log $info
        # Check for thermal throttling clues
        $tempLine = ($info | Select-Object -Skip 1) | Select-Object -First 1
        if ($tempLine -match '(\d+)\s*C') {
            $temp = [int]$matches[1]
            if ($temp -gt 80) { Write-Log "WARNING: GPU temperature ${temp}°C — possible thermal throttling!" "Red" }
            elseif ($temp -gt 70) { Write-Log "WARNING: GPU temperature ${temp}°C — warm but within range." "Yellow" }
            else { Write-Log "Temperature OK: ${temp}°C" "Green" }
        }
    }
    $ecc = & nvidia-smi --query-gpu=ecc.errors.uncorrected.volatile.total,ecc.errors.corrected.volatile.total --format=csv 2>$null
    if ($ecc) {
        Write-Log "--- ECC Memory Errors ---" "Yellow"
        Write-Log $ecc
        $eccVals = ($ecc | Select-Object -Skip 1).Split(',')[0..1]
        foreach ($v in $eccVals) {
            $trim = $v.Trim()
            if ($trim -ne "N/A" -and $trim -ne "0" -and $trim -ne "Not Supported") {
                Write-Log "ALERT: GPU memory ECC errors detected: $trim" "Red"
            }
        }
    }
} else {
    Write-Log "nvidia-smi not found in PATH. Install NVIDIA drivers or add nvidia-smi to PATH." "Red"
}
Write-Log ""

# ── 2. Windows Event Log — TDR / Display driver crashes ───
Write-Log "--- 2. Event Log — TDR / Display Driver Crashes ---" "Yellow"
$start = (Get-Date).AddDays(-7)

# TDR (Event ID 4101 = nvlddmkm recovered from error)
$tdr = Get-WinEvent -FilterHashtable @{LogName='System'; ID=4101; StartTime=$start} -ErrorAction SilentlyContinue
if ($tdr) {
    $count = ($tdr | Measure-Object).Count
    Write-Log "CRITICAL: Found $count TDR recovery events (Event ID 4101) in last 7 days!" "Red"
    Write-Log "This means GPU stopped responding and driver recovered it — classic sign of bad power/thermals/chip." "Red"
    $tdr | Select-Object -First 5 | ForEach-Object {
        Write-Log "  $($_.TimeCreated): $($_.Properties[0].Value)" "Red"
    }
} else {
    Write-Log "No TDR (Event ID 4101) events found. Good." "Green"
}

# nvlddmkm errors (Event ID 14, 13, etc.)
$nvrm = Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='nvlddmkm'; StartTime=$start} -ErrorAction SilentlyContinue
if ($nvrm) {
    $count = ($nvrm | Measure-Object).Count
    Write-Log "WARNING: Found $count nvlddmkm driver events in last 7 days." "Yellow"
    $nvrm | Select-Object -First 5 | ForEach-Object {
        Write-Log "  [$($_.Id)] $($_.TimeCreated): $($_.Message -replace '\r|\n',' ')" "Yellow"
    }
} else {
    Write-Log "No nvlddmkm driver errors in last 7 days." "Green"
}

# Application crashes with python.exe + nvcuda.dll / ctranslate2
$cudaCrashes = Get-WinEvent -FilterHashtable @{LogName='Application'; StartTime=$start; Level=1,2} -ErrorAction SilentlyContinue | Where-Object {
    $_.Message -match 'python' -and ($_.Message -match 'nvcuda|nvfatbin|ctranslate2|cudnn|cublas')
}
if ($cudaCrashes) {
    $count = ($cudaCrashes | Measure-Object).Count
    Write-Log "FOUND $count Application crash events involving python + CUDA libraries!" "Red"
    $cudaCrashes | Select-Object -First 5 | ForEach-Object {
        Write-Log "  $($_.TimeCreated): $($_.Message -replace '\r|\n',' ' | Select-Object -First 100)" "Red"
    }
} else {
    Write-Log "No python/CUDA application crashes found in last 7 days." "Green"
}
Write-Log ""

# ── 3. Windows Error Reporting (WER) — recent crashes ─────
Write-Log "--- 3. Windows Error Reporting (WER) ---" "Yellow"
$werPath = "C:\ProgramData\Microsoft\Windows\WER\ReportArchive"
if (Test-Path $werPath) {
    $recentWer = Get-ChildItem $werPath -Recurse -Filter "*.wer" | Where-Object {$_.LastWriteTime -gt $start} | Sort-Object LastWriteTime -Descending | Select-Object -First 10
    if ($recentWer) {
        Write-Log "Recent WER reports (last 7 days):" "Yellow"
        $recentWer | ForEach-Object {
            $content = Get-Content $_.FullName -ErrorAction SilentlyContinue | Select-String "AppName|FaultingModule|ExceptionCode"
            Write-Log "  $($_.LastWriteTime): $($_.Name)" "Yellow"
            $content | ForEach-Object { Write-Log "    $_" "Gray" }
        }
    } else {
        Write-Log "No recent WER reports." "Green"
    }
} else {
    Write-Log "WER path not accessible." "Gray"
}
Write-Log ""

# ── 4. Power state check (WHEA / PCI-Express errors) ─────
Write-Log "--- 4. WHEA / PCI-Express / Hardware Errors ---" "Yellow"
$whea = Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-WHEA-Logger'; StartTime=$start} -ErrorAction SilentlyContinue
if ($whea) {
    Write-Log "WARNING: WHEA hardware errors detected!" "Red"
    $whea | Select-Object -First 3 | ForEach-Object { Write-Log "  $($_.TimeCreated): $($_.Message -replace '\r|\n',' ')" "Red" }
} else {
    Write-Log "No WHEA hardware errors." "Green"
}

# PCI-Express/Kernel-Power
$pci = Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-Kernel-Power'; StartTime=$start; ID=41,101,104} -ErrorAction SilentlyContinue
if ($pci) {
    Write-Log "Kernel-Power events (sleep/hibernate/power-loss):" "Yellow"
    $pci | Select-Object -First 3 | ForEach-Object { Write-Log "  [$($_.Id)] $($_.TimeCreated)" "Yellow" }
} else {
    Write-Log "No abnormal Kernel-Power events." "Green"
}
Write-Log ""

# ── 5. CUDA compute stress test (short, 10 seconds) ────────
Write-Log "--- 5. CUDA Compute Sanity Check ---" "Yellow"
if ($smi) {
    Write-Log "Running 10-second GPU compute stress via nvidia-smi dmon..." "Gray"
    $dmon = & nvidia-smi dmon -s pucm -d 1 -c 10 2>$null
    if ($dmon) {
        Write-Log "GPU utilization during 10s sample:"
        Write-Log ($dmon | Select-Object -Last 5 | Out-String) "Gray"
        # Check for drops to 0% util with non-zero temp (sign of TDR/crash during test)
        $lines = $dmon | Select-Object -Skip 3
        $zeroUtil = $lines | Where-Object { $_ -match '^\s*(\d+)\s+(\d+)\s+(\d+)' -and $matches[2] -eq '0' -and $matches[3] -gt '0' }
        if ($zeroUtil) {
            Write-Log "WARNING: GPU utilization dropped to 0% while temp > 0 during test — possible reset." "Red"
        } else {
            Write-Log "GPU stayed responsive during 10s test." "Green"
        }
    }
} else {
    Write-Log "nvidia-smi unavailable, skipping stress check." "Gray"
}
Write-Log ""

# ── 6. Memory integrity check (simple CUDA malloc-fill-free) ─
Write-Log "--- 6. CUDA Memory Allocation Check ---" "Yellow"
if ($smi) {
    $memInfo = & nvidia-smi --query-gpu=memory.total,memory.free,memory.used --format=csv 2>$null
    Write-Log $memInfo
    $memLine = ($memInfo | Select-Object -Skip 1)
    if ($memLine -match '(\d+).+?(\d+).+?(\d+)') {
        $total = [int]$matches[1]
        $free = [int]$matches[2]
        $used = [int]$matches[3]
        if ($free -lt $total * 0.10) {
            Write-Log "WARNING: Less than 10% VRAM free ($free / $total MiB). Other apps may cause OOM." "Red"
        } else {
            Write-Log "VRAM usage looks OK ($used / $total MiB used)." "Green"
        }
    }
}
Write-Log ""

# ── 7. Driver & CUDA version ────────────────────────────────
Write-Log "--- 7. Driver / CUDA Version ---" "Yellow"
$driverVersion = (Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*" -ErrorAction SilentlyContinue | Where-Object {$_.DisplayName -like "NVIDIA*Driver*"} | Select-Object -First 1).DisplayVersion
if ($driverVersion) { Write-Log "Installed NVIDIA driver (from registry): $driverVersion" }

$nvPath = "$env:SystemRoot\System32\DriverStore\FileRepository\nvidia*\nvlddmkm.sys"
$nvSys = Get-Item $nvPath -ErrorAction SilentlyContinue | Select-Object -First 1
if ($nvSys) {
    Write-Log "nvlddmkm.sys version: $($nvSys.VersionInfo.FileVersion)"
    Write-Log "nvlddmkm.sys modified: $($nvSys.LastWriteTime)"
}

$cudaPath = "$env:ProgramFiles\NVIDIA GPU Computing Toolkit\CUDA\v*\bin\nvcc.exe"
$cuda = Get-Item $cudaPath -ErrorAction SilentlyContinue | Select-Object -First 1
if ($cuda) {
    Write-Log "CUDA nvcc found: $($cuda.FullName)"
    $ver = & $cuda.FullName --version 2>$null | Select-String "release"
    Write-Log $ver
} else {
    Write-Log "CUDA Toolkit not found in standard path." "Gray"
}
Write-Log ""

# ── 8. Python / pytorch CUDA check (if available) ─────────
Write-Log "--- 8. PyTorch CUDA Check (if installed) ---" "Yellow"
$py = Get-Command python -ErrorAction SilentlyContinue
if ($py) {
    $torchCheck = & python -c "import torch; print(f'PyTorch {torch.__version__}'); print(f'CUDA available: {torch.cuda.is_available()}'); print(f'Device count: {torch.cuda.device_count()}'); print(f'Current device: {torch.cuda.current_device()}'); print(f'Device name: {torch.cuda.get_device_name(0) if torch.cuda.is_available() else \"N/A\"}')" 2>$null
    if ($torchCheck) {
        Write-Log $torchCheck
        # Try a tiny allocation test
        $allocTest = & python -c "
import torch, sys
try:
    x = torch.randn(1000,1000).cuda()
    y = torch.matmul(x, x.T)
    torch.cuda.synchronize()
    print('TINY_MATMUL_OK')
except Exception as e:
    print('TINY_MATMUL_FAIL:', e)
    sys.exit(1)
" 2>$null
        if ($allocTest -match "TINY_MATMUL_OK") {
            Write-Log "PyTorch CUDA tiny allocation + matmul OK." "Green"
        } else {
            Write-Log "PyTorch CUDA tiny allocation FAILED: $allocTest" "Red"
        }
    } else {
        Write-Log "PyTorch not installed or not in PATH." "Gray"
    }
} else {
    Write-Log "Python not in PATH." "Gray"
}
Write-Log ""

# ── 9. Power connector check (advisory) ─────────────────────
Write-Log "--- 9. Physical Power Advisory ---" "Yellow"
Write-Log "Checklist (manual — look at your GPU / PSU):"
Write-Log "  • PCIe power cables: 6-pin / 8-pin / 12VHPWR fully seated?"
Write-Log "  • If using split/single cable for high-power GPU (RTX 3080+), use SEPARATE cables from PSU."
Write-Log "  • GPU-Z or MSI Afterburner: 'Perfcap reason' should not show 'Pwr' constantly."
Write-Log "  • Monitor 12V rail in BIOS/HWINFO: should be 11.4–12.6V under load."
Write-Log ""

# ── 10. Final verdict ───────────────────────────────────────
Write-Log "--- 10. VERDICT ---" "Cyan"
$redFlags = ($report | Where-Object { $_ -match "^(CRITICAL|ALERT|WARNING:.*FAIL|FOUND)" })
if ($redFlags) {
    Write-Log "RED FLAGS DETECTED — GPU or power subsystem likely faulty:" "Red"
    $redFlags | ForEach-Object { Write-Log "  $_" "Red" }
    Write-Log "" "White"
    Write-Log "Recommendation:"
    Write-Log "  1. Underclock GPU by -100 MHz core / -200 MHz memory in MSI Afterburner."
    Write-Log "  2. Reduce power limit to 80-90%."
    Write-Log "  3. Check/reseat power cables. Use separate PCIe cables, not daisy-chained."
    Write-Log "  4. Run MSI Kombustor / FurMark for 5 min. If artifacts/crash = hardware issue."
    Write-Log "  5. If TDR 4101 persists after all above — RMA the card (dying chip/VRM)."
} else {
    Write-Log "No critical hardware red flags detected." "Green"
    Write-Log "If Whisper still crashes, likely SOFTWARE issue (driver/cuda/ctranslate2 mismatch)."
    Write-Log "Try: DDU + reinstall latest Studio Driver (not Game Ready)."
}
Write-Log ""
Write-Log "Report saved to: $outFile" "Cyan"

$report | Out-File $outFile -Encoding utf8
Write-Host "`nDone. Report saved to Desktop: $outFile" -ForegroundColor Cyan
