# John Piper SHORTS — VoxCPM2 CPU

Прямой production-конвейер без ZIP, Base64 и вложенных launchers.

- John Piper — исходный спикер.
- VoxCPM2 — модель синтеза.
- CPU only; CUDA скрыта до импорта torch.
- Zero-shot voice clone из исходного ролика.
- Пять смысловых блоков, NoChew и multi-candidate selection.
- Каждый завершённый блок сохраняет checkpoint: после остановки повторно синтезируются только незавершённые блоки.
- Постоянный английский оригинал 18%, master -14 LUFS / -1 dBTP.

## Запуск

```powershell
cd "C:\Users\Fedor\Projects\mp3telegrambot"
git pull origin main
Set-ExecutionPolicy -Scope Process Bypass -Force
.\tools\voxcpm2\examples\john_piper_z20py4yqhyq\Run-John-Piper-FINAL-CPU.ps1
```

Итоговый файл:

```text
C:\AI-Archive\John-Piper-Short-Z20Py4yQhYQ-FINAL\output\John_Piper_Russian_Dub_FINAL_UPLOAD.mp4
```
