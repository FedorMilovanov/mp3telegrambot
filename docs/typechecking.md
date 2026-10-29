# Проверка типов: strict для всего репозитория

Строгий `mypy` включён для всей рабочей копии. Production-only этап завершён:
entrypoints, `core/`, `services/`, `pipelines/`, `handlers/`, `converters/`,
`scripts/`, `tools/` и `tests/` входят в один обязательный gate.

Repository-wide срез — это контракт: ошибок **ноль**, сужать его обратно до
production modules или скрывать новые файлы через `--exclude` нельзя.

## Что проверяется

| Срез | Ошибок `mypy --strict` | Исключения по умолчанию |
|---|---|---|
| `.` | 0 | нет |

Команда `python tools/check_types.py` запускает `python -m mypy .`; настройки
строгости находятся только в `[tool.mypy]`. Проверка охватывает 925 модулей в
обычном module resolution mypy. Произвольный путь по-прежнему можно передать
аргументом для сфокусированной диагностики.

## Byte-stable historical LiveDub QA

`tests/livedub_qa_cases.py` намеренно сохраняется byte-for-byte: Ruff его не
переписывает, а устаревшие контракты заменяются в существующем collector
`tests/test_livedub_qa.py`. Для статического интерфейса historical module
рядом находится полный `tests/livedub_qa_cases.pyi`: все test functions имеют
`-> None`, fixture parameters типизированы как `pytest.MonkeyPatch` и `Path`,
а два Telegraph helper-а описывают динамическую node-границу через `list[Any]`.

Это стандартное разделение implementation и `.pyi`-интерфейса, а не изменение
strict-конфигурации или регекс-исключение. Stub не подменяет выполнение:
collector по-прежнему загружает historical implementation через `runpy` и
экспортирует все незаменённые cases в pytest. Их выполнение обязательно;
наличие collector wiring само по себе доказательством coverage не считается.

## Как запускать

```bash
python tools/check_types.py             # весь репозиторий (то же, что в CI)
python tools/check_types.py core tools # сфокусированная диагностика
python tools/check_types.py --verbose   # команда и полный вывод mypy
python -m mypy .                        # непосредственный полный census
```

Локально `python -m pytest -q` тоже падает при ошибке типа:
`tests/test_typecheck_gate.py` запускает mypy тем же кодом, что и CI.

## Где живёт строгость

Настройки — в `[tool.mypy]` (`pyproject.toml`), а не в командной строке CI:

| Настройка | Зачем |
|---|---|
| `strict = true` | обязательны аннотации и проверка тел функций |
| `explicit_package_bases = true` | предотвращает duplicate module от корневого пакета |
| `follow_imports = "silent"` | показывает ошибки вызывающего кода без повторного шума зависимостей |
| `enable_error_code = ["ignore-without-code"]` | разрешает только адресные suppressions |
| `python_version = "3.12"` | совпадает с поддерживаемым floor проекта |

`tests/test_typecheck_gate.py` проверяет, что эти значения не ослаблены.
Локальная версия интерпретатора может отличаться; authoritative matrix в CI —
Python 3.12/3.13 плюс Windows-sensitive jobs.

## Политика подавлений

`# type: ignore` — не способ пройти gate, а утверждение о конкретной границе.

1. Код ошибки обязателен: `# type: ignore[attr-defined]`, а не голый ignore.
2. Рядом должна быть причина; blanket suppressions и ослабление конфигурации
   не допускаются.
3. Если ошибка указывает на настоящий баг, исправляется код и выполняются
   поведенческие тесты. Для Telegram/FFmpeg test doubles приведения типов
   остаются на проверенной fake boundary, а production API не расширяются до
   тестовых классов.
4. Async и security findings не исправляются вслепую: worker ownership,
   cancellation, cleanup и постусловия подтверждаются выполненными тестами.

## Поддержание gate

1. Новый Python-файл автоматически попадает в `mypy .`; отдельного разрешения
   или добавления в список не требуется.
2. Перед push запускаются focused tests для изменённого поведения, Ruff,
   форматирование и `python tools/check_types.py`.
3. Полный handoff дополнительно проходит `tools/verify_repo.py`, coverage
   ratchet, diff-cover и authoritative CI. Локальный успех не заменяет
   Python 3.12/3.13 или Windows evidence.
