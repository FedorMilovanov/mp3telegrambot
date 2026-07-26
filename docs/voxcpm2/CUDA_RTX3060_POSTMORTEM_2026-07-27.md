# VoxCPM2 CUDA / RTX 3060 — итог диагностики 2026-07-27

## Решение

На текущем компьютере **VoxCPM2 разрешена только на CPU**. Production-launchers обязаны скрывать GPU через `CUDA_VISIBLE_DEVICES=-1` и проверять `torch.cuda.is_available() == False`.

Повторять VoxCPM2 CUDA-прогоны без отдельного ремонта или независимой проверки видеокарты не следует.

## Конфигурация наблюдения

- GPU: NVIDIA GeForce RTX 3060 12 ГБ;
- драйвер: 610.62;
- PyTorch: проверялись 2.7.1+cu126 и 2.11.0+cu126;
- CUDA runtime: 12.6;
- power limit во время ограниченных прогонов: 100 Вт;
- модель помещалась в VRAM, поэтому ошибки не были OOM.

## Что проходило

- создание свежего CUDA-контекста;
- небольшой GPU→CPU memory round-trip и synchronize;
- загрузка VoxCPM2 без генерации;
- ограниченные FP32/FP16/BF16 синтетические операции;
- отдельный BF16 `F.linear`/GEMM probe.

## Что повторяемо падало

Реальная модельная последовательность VoxCPM2 вызывала отказ драйвера и новые `nvlddmkm Event ID 153`:

1. reference AudioVAE BF16 `Conv1d`;
2. zero-shot без reference audio — BF16 `F.linear/cublasGemmEx`;
3. PyTorch 2.11 с отключённым cuDNN — BF16 `F.linear/cublasGemmEx`;
4. принудительный FP16 — `q_proj/F.linear/cublasGemmEx` с `CUDA_R_16F`.

Появлялись ошибки `illegal memory access`, `CUBLAS_STATUS_INTERNAL_ERROR` или `CUBLAS_STATUS_EXECUTION_FAILED`, после которых Windows записывала новые Event ID 153.

## Что исключено

Проверками исключены как единственная причина:

- нехватка VRAM;
- конкретный reference WAV;
- только AudioVAE;
- только cuDNN;
- только BF16;
- только PyTorch 2.7.1;
- слишком высокий power limit;
- старый повреждённый CUDA-контекст.

## Эксплуатационный контракт

- синтез и zero-shot voice cloning: CPU;
- промежуточный звук: 24-bit WAV;
- финальная сборка: FFmpeg CPU/video-copy;
- GPU не является fallback для синтеза;
- старые одноразовые CUDA probes удалены из рабочего дерева после фиксации этого отчёта;
- диагностические ZIP и Event RecordId остаются во внешнем архиве оператора.

## Production-примеры

- John MacArthur: принятая CPU-схема segmented zero-shot clone;
- John Piper: `tools/voxcpm2/examples/john_piper_z20py4yqhyq/`;
- команда John Piper описана в локальном `README_RU.md` примера.
