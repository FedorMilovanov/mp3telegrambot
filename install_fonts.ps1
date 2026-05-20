#Requires -RunAsAdministrator
# Установка шрифтов Montserrat ExtraBold (и Black) для красивых субтитров Shorts

$ErrorActionPreference = "Stop"
$fontsUrl = "https://fonts.google.com/download?family=Montserrat"
$zipPath = "$env:TEMP\Montserrat.zip"
$extractPath = "$env:TEMP\Montserrat"

Write-Host "⬇️  Скачиваю Montserrat с Google Fonts..." -ForegroundColor Cyan
Invoke-WebRequest -Uri $fontsUrl -OutFile $zipPath -UseBasicParsing

Write-Host "📦 Распаковываю..." -ForegroundColor Cyan
Expand-Archive -Path $zipPath -DestinationPath $extractPath -Force

# Ищем ExtraBold и Black
$fontFiles = Get-ChildItem -Path $extractPath -Recurse -Filter "Montserrat-ExtraBold.ttf"
if (-not $fontFiles) {
    # fallback: ищем в static/
    $fontFiles = Get-ChildItem -Path "$extractPath\static" -Filter "Montserrat-ExtraBold.ttf" -ErrorAction SilentlyContinue
}

if (-not $fontFiles) {
    Write-Host "❌ Montserrat-ExtraBold.ttf не найден в архиве. Содержимое:" -ForegroundColor Red
    Get-ChildItem -Path $extractPath -Recurse | Select-Object -ExpandProperty FullName
    exit 1
}

$shell = New-Object -ComObject Shell.Application
$fontsFolder = $shell.Namespace(0x14) # FOLDERID_Fonts

foreach ($font in $fontFiles) {
    Write-Host "🅰️  Устанавливаю $($font.Name)..." -ForegroundColor Green
    $fontsFolder.CopyHere($font.FullName, 0x10) # 0x10 = "Don't prompt"
}

# Дополнительно установим Montserrat-Black.ttf если есть
$blackFiles = Get-ChildItem -Path $extractPath -Recurse -Filter "Montserrat-Black.ttf" -ErrorAction SilentlyContinue
if ($blackFiles) {
    foreach ($font in $blackFiles) {
        Write-Host "🅰️  Устанавливаю $($font.Name)..." -ForegroundColor Green
        $fontsFolder.CopyHere($font.FullName, 0x10)
    }
}

Write-Host "✅ Готово! Перезапусти бота — в логе должно появиться: 🅰️ Шрифт субтитров: Montserrat ExtraBold" -ForegroundColor Green
