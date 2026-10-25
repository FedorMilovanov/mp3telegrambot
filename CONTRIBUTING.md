# Как вносить изменения

Проект личный, но правила те же, что в командном: они существуют не для
бюрократии, а потому что каждый из них закрывает конкретный способ сломать прод.

## Окружение

```bash
# Python 3.12 или 3.13 (floor — 3.12), FFmpeg/FFprobe в PATH, Node 22+ для yt-dlp
python -m venv .venv && . .venv/bin/activate     # Windows: .venv\Scripts\activate
python -m pip install -r requirements-lock.txt   # lock, а не floor-файлы
pre-commit install && pre-commit install --hook-type pre-push
```

Ставить нужно **lock**, а не `requirements.txt`: в lock'е точные версии, на
которых гоняется CI. `requirements.txt` и `requirements-dev.txt` — объявления
границ, которые обновляются первыми при изменении зависимости.

## Что гонять перед push

| Команда | Что проверяет | Сколько |
|---|---|---|
| `python -m pytest -q` | весь набор | ~90 с |
| `python -m ruff check .` | гейт линтера | ~2 с |
| `python tools/check_types.py` | `mypy --strict` по объявленному срезу (`core/`, `pipelines/`) | ~2 с (кэш) |
| `python tools/check_import_layers.py` | направление production-импортов и burn-down legacy-рёбер | ~1 с |
| `python tools/check_coverage_ratchet.py` | покрытие объявленных файлов не упало (нужен `coverage.xml`) | ~1 с |
| `python tools/verify_repo.py` | compileall, генерируемые артефакты, pytest, ruff, типы, слои, `git diff --check` | ~100 с |
| `pre-commit run` | hooks на staged-файлах: форматирование, YAML, большие файлы, конфликты | ~10 с |

Для проверки файлов уже созданных коммитов ветки используйте
`pre-commit run --from-ref origin/main --to-ref HEAD`; для произвольного набора —
`pre-commit run --files <paths>`. Пока не проведена отдельная repo-wide волна
форматирования, не заменяйте эти команды на `pre-commit run --all-files`:
записывающий `ruff format` иначе переформатирует весь легаси-код.

`pre-commit` на `pre-push` запускает `verify_repo.py` автоматически — это ровно
те проверки, из-за которых PR бывает красным, хотя локально «всё работало».

## Генерируемые артефакты

Три документа собираются из кода и **не редактируются руками**. После изменения
кода или документации:

```bash
python scripts/render_env_reference.py      # docs/env-reference.md
python scripts/render_model_catalog.py      # docs/model-catalog.md
python tools/render_docs_index.py           # docs/README.md
```

Каждый поддерживает `--check`; CI падает, если артефакт устарел.

## Изменение зависимостей

1. Правка границ в `requirements.txt` / `requirements-dev.txt`.
2. Точный пин в `requirements-lock.txt`.
3. `python tools/check_requirements_lock.py` — lock-контракт.
4. `python tools/audit_requirements_lock.py` — известные уязвимости (нужен `pip-audit`).
5. Полный прогон тестов.

Правила и исключения — [`docs/dependency-policy.md`](docs/dependency-policy.md).
Workflow не ставит пакеты в обход lock'а: это проверяет
`tests/test_workflows_install_from_lock.py`.

## Правила PR

- **один пункт — один PR**: смешение смены floor'а, обновления зависимостей и
  рефакторинга не даёт понять, что именно сломалось;
- новый код идёт с тестами; изменённые строки проверяются `diff-cover` (порог 75 %);
- тест обязан падать без исправления. Если он зелёный и до, и после — он ничего
  не проверяет;
- утверждения в документации проверяются командой. «Не читается», «не работает»,
  «нет покрытия» — это гипотезы, пока не показан вывод;
- если поведение меняется — обновите документацию **в том же PR**, иначе она
  начнёт врать.

## Версии и релизы

Версия объявлена в `core/version.py` (SemVer), печатается командой
`mp3bot --version`, попадает в метаданные wheel. Повышение версии — отдельный
коммит с записью в `CHANGELOG.md`.

## Документация

- индекс `docs/README.md` генерируется: новый файл в `docs/` появится в нём сам;
- имена файлов описывают область и дату; суффикс `_latest` запрещён (проверяется
  `tools/render_docs_index.py --check`);
- устройство проекта — [`ARCHITECTURE.md`](ARCHITECTURE.md), правила безопасности —
  [`SECURITY.md`](SECURITY.md).

## Стиль

- `ruff` — единственный линтер; конфигурация в `pyproject.toml`;
- форматирование — `ruff format` (запускается в `pre-commit` на изменённых файлах);
- аннотации типов обязательны для нового кода: 92 % существующих функций уже
  аннотированы, и проверка типов вводится постепенно;
- в комментариях объясняется **почему**, а не что: код говорит «что» сам.
