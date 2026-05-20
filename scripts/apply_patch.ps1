# apply_patch.ps1 — применяет .patch файл с проверкой
# Использование: .\dev-tools\scripts\apply_patch.ps1 <имя_патча>
# Пример: .\dev-tools\scripts\apply_patch.ps1 2026-05-20_deep_quality

param(
    [Parameter(Mandatory=$true)]
    [string]$PatchName
)

# Добавляем .patch если не указан
if (-not $PatchName.EndsWith(".patch")) {
    $PatchName = "$PatchName.patch"
}

$PatchPath = "dev-tools/patches/$PatchName"

if (-not (Test-Path $PatchPath)) {
    Write-Host "❌ Патч не найден: $PatchPath" -ForegroundColor Red
    Write-Host ""
    Write-Host "Доступные патчи:" -ForegroundColor Cyan
    Get-ChildItem "dev-tools/patches" -Filter "*.patch" | ForEach-Object {
        Write-Host "  - $($_.Name)" -ForegroundColor White
    }
    exit 1
}

Write-Host "🔍 Проверяю патч (dry-run): $PatchName" -ForegroundColor Cyan
$checkResult = git apply --check $PatchPath 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ Патч НЕ применится:" -ForegroundColor Red
    Write-Host $checkResult -ForegroundColor Red
    Write-Host ""
    Write-Host "Возможные причины:" -ForegroundColor Yellow
    Write-Host "  - патч уже применён (проверь dev-tools/docs/CHANGELOG_PATCHES.md)" -ForegroundColor White
    Write-Host "  - файлы изменились (используй --3way или примени вручную)" -ForegroundColor White
    Write-Host ""
    Write-Host "Попробовать с 3way merge?" -ForegroundColor Cyan
    Write-Host "  git apply --3way $PatchPath" -ForegroundColor White
    exit 1
}
Write-Host "  ✅ Патч применится чисто" -ForegroundColor Green
Write-Host ""

Write-Host "🚀 Применяю..." -ForegroundColor Cyan
git apply $PatchPath
if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ Ошибка при применении" -ForegroundColor Red
    exit 1
}

Write-Host "✅ Готово!" -ForegroundColor Green
Write-Host ""
Write-Host "Следующие шаги:" -ForegroundColor Cyan
Write-Host "  1. py -3.13 bot_new.py    # проверить что всё работает" -ForegroundColor White
Write-Host "  2. .\dev-tools\scripts\cleanup_backups.ps1   # очистить мусор если есть" -ForegroundColor White
Write-Host "  3. git add -A; git commit -m 'apply: $PatchName'" -ForegroundColor White
