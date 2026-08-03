# TTS operations

Главный документ по безопасной эксплуатации VoxCPM2 CPU, экспериментам с MOSS-TTS и восстановлению Dub Studio проектов:

- [`docs/voxcpm2_cpu_operations.md`](docs/voxcpm2_cpu_operations.md)

Штатный ремонт одного `request.json`:

```powershell
py -3.13 -m tools.voxcpm2.repair_project_request `
  --project-root "C:\AI-Archive\MP3Bot-Dub-Studio\projects\<project-id>" `
  --write `
  --write-notes
```

Перед запуском TTS обязательно получить `CONFIG VALID`. Не переустанавливать модель из-за конфликта параметров или ошибочного пути. Не смешивать Python бота, VoxCPM2 venv и окружения экспериментальных моделей.

Этот файл является коротким указателем. Подробные правила, известные ошибки, безопасная очистка, пути, параметры и решения по моделям хранятся только в основном runbook, чтобы документация не расходилась.
