# Windows runner для реального TTS weighted smoke

Этот runbook относится только к manual workflow `TTS Weighted Smoke`. Он не нужен hosted CI и не должен использоваться для запуска кода из pull request.

## 1. Зарегистрировать runner

В настройках GitHub Actions создайте Windows x64 self-hosted runner для:

- репозитория `FedorMilovanov/mp3telegrambot`; или
- организации `FedorMilovanov`, если runner разрешён этому репозиторию.

Используйте официальный пакет GitHub Actions Runner и команды, которые GitHub показывает на странице добавления runner. Регистрационный token краткоживущий: не сохраняйте его в репозитории, issue, `.env`, Actions variables или логах.

При конфигурации:

- установите runner как Windows service;
- оставьте runner persistent, не ephemeral;
- добавьте custom label `tts-weights`;
- рекомендуемый каталог runner — короткий путь у корня диска, например `C:\actions-runner`.

Workflow требует полный набор labels:

```text
self-hosted, Windows, X64, tts-weights
```

Официальные инструкции:

- https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/add-runners
- https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/configure-the-application
- https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/apply-labels

## 2. Подготовить локальные ресурсы

На runner должны существовать:

- trusted Python executable с locked runtime dependencies;
- production VoxCPM2 model directory;
- локальный reference WAV;
- `ffprobe` в `PATH` службы runner.

Reference WAV и модель не копируются в репозиторий и не загружаются в Actions artifacts.

## 3. Применить machine environment

Откройте **PowerShell 7 от имени администратора** в checkout `main` и выполните:

```powershell
./tools/voxcpm2/Prepare-TTSWeightsRunner.ps1 `
  -RunnerDirectory '<RUNNER_DIRECTORY>' `
  -PythonExecutable '<TRUSTED_PYTHON_EXE>' `
  -ModelDirectory '<VOXCPM2_MODEL_DIRECTORY>' `
  -ReferenceWav '<REFERENCE_WAV>' `
  -WorkDirectory '<EMPTY_SETUP_WORK_DIRECTORY>' `
  -Repository 'FedorMilovanov/mp3telegrambot' `
  -ProfileId 'voxcpm2-production-v1' `
  -Mode Apply
```

`Apply`:

1. требует elevated session и `ShouldProcess` confirmation;
2. записывает только `TTS_SMOKE_PYTHON`, `TTS_SMOKE_MODEL_ROOT`, `TTS_SMOKE_REFERENCE_WAV` в Machine environment;
3. перезапускает службу, имя которой строго прочитано из runner `.service`;
4. запускает repository provisioning checker и no-weights doctor;
5. оставляет только privacy-safe `setup-report.json`.

Скрипт не принимает GitHub token, registration token или PAT и не меняет server-side labels.

## 4. Повторная read-only проверка

После перезагрузки машины или изменения модели выполните тот же вызов с:

```powershell
-Mode Validate
```

`Validate` не изменяет environment и не перезапускает службу. Он требует, чтобы:

- `.runner` был строгим JSON;
- registration scope соответствовал репозиторию или организации-владельцу;
- runner был persistent;
- `.service` имел безопасный формат;
- Windows service уже находился в `Running`;
- три Machine bindings точно совпадали с переданными ресурсами;
- doctor подтвердил imports, model discovery, reference WAV, `ffprobe`, offline policy и atomic storage;
- `weights_loaded=false` и `session_opened=false`.

Для каждого запуска используйте новый пустой `WorkDirectory` либо удалите предыдущий каталог после проверки.

## 5. Первый реальный synthesis

В GitHub Actions вручную запустите workflow `TTS Weighted Smoke` на ветке `main`:

```text
profile_id: voxcpm2-production-v1
duration_budget: 4.0
```

Job не должен оставаться queued. Если он queued, сначала проверьте online status runner и custom label `tts-weights`; не ослабляйте `runs-on` в workflow.

Успешный run должен оставить ровно один artifact:

```text
tts-weighted-smoke-attestation-<run_id>-<attempt>
```

Внутри допускается только `attestation.json`. WAV, reference voice, model files, raw doctor/smoke reports и execution JSONL сохраняться не должны.

## 6. Закрытие issue #72

Issue закрывается только после успешного реального run. В комментарий добавляются:

- ссылка на Actions run;
- commit SHA из attestation subject;
- attestation digest;
- подтверждение `audio_retained=false`.

Локальные пути, runner service account, reference voice и содержимое model directory в issue не публикуются.
