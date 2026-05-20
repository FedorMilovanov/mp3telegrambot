# cleanup_backups.ps1 — удаляет .bak_* файлы и SQLite WAL/SHM из репо
# Запуск: .\dev-tools\scripts\cleanup_backups.ps1

Write-Host "🧹 Cleanup: ищу мусор в репо..." -ForegroundColor Cyan

$root = (Get-Item .).FullName
$removed = 0

# .bak_* файлы (от наших патчей) — все варианты
Get-ChildItem -Path $root -Recurse -File 2>$null | Where-Object {
    $_.Name -match '\.(bak_[a-zA-Z0-9_]+|bak|py\.bak.*)$' -and $_.FullName -notlike "*\.git\*"
} | ForEach-Object {
    Write-Host "  [del] $($_.FullName.Replace($root, '.'))" -ForegroundColor Yellow
    Remove-Item $_.FullName -Force
    $removed++
}

# SQLite WAL/SHM
foreach ($f in @("bot_cache.db-shm", "bot_cache.db-wal", "bot_cache.db-journal")) {
    if (Test-Path $f) {
        Write-Host "  [del] $f" -ForegroundColor Yellow
        Remove-Item $f -Force
        $removed++
    }
}

# Старые fix_*.py / apply_*.py / diagnose_*.py в корне
Get-ChildItem -Path $root -File 2>$null | Where-Object {
    $_.Name -match '^(fix|apply|diagnose)_.*\.py$'
} | ForEach-Object {
    Write-Host "  [del] $($_.Name) (перенеси в dev-tools/scripts/ если нужно сохранить)" -ForegroundColor Yellow
    Remove-Item $_.FullName -Force
    $removed++
}

Write-Host ""
if ($removed -gt 0) {
    Write-Host "✅ Удалено: $removed файлов" -ForegroundColor Green
    Write-Host ""
    Write-Host "Не забудь закоммитить очистку:" -ForegroundColor Cyan
    Write-Host "  git add -A" -ForegroundColor White
    Write-Host "  git commit -m 'chore: cleanup backups'" -ForegroundColor White
} else {
    Write-Host "✅ Чисто, мусора нет" -ForegroundColor Green
}
