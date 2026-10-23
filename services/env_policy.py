#!/usr/bin/env python3
"""Единственный владелец переменных окружения, которые форсирует старт.

Проблема, которую закрывает модуль: значения вроде ``GEMINI_MODEL`` или
``WHISPER_MODEL`` описаны в ``.env.example`` как настройки, но
``services/gemini_max_quality.py`` и ``services/livedub_quality_runtime.py``
перезаписывали их безусловно при старте. Оператор менял переменную, перезапускал
бота и не видел ничего: ни эффекта, ни сообщения. Двадцать таких «мёртвых ручек»
— это не мелкая косметика, а ложь в документации.

Форсирование остаётся (контракт качества не должен отключаться случайным
значением из старого `.env`), но становится наблюдаемым:

* ``force()`` запоминает значение оператора и пишет WARNING, если оно отличается;
* ``POLICY_OWNED`` — реестр «переменная → кто владеет» (кортеж владельцев:
  одну и ту же переменную могут форсировать несколько политик), по нему
  проверяются ``.env.example`` и тесты;
* ``overridden()`` отдаёт список фактических подмен для диагностики при старте.

Никаких импортов проекта здесь нет: модуль вызывается до ``core.globals``.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

#: Переменная окружения -> владельцы (в порядке первого форсирования).
POLICY_OWNED: dict[str, tuple[str, ...]] = {}

#: Переменная -> (значение оператора, принудительное значение).
_OVERRIDDEN: dict[str, tuple[str, str]] = {}


def force(name: str, value: str, *, owner: str) -> str:
    """Принудительно установить ``name=value`` и сообщить о подмене.

    Args:
        name: имя переменной окружения.
        value: значение, которое обязана увидеть остальная часть кода.
        owner: короткий идентификатор политики (для лога и реестра).

    Returns:
        Установленное значение (удобно для построения диагностической строки).
    """
    name = str(name)
    value = str(value)
    owners = POLICY_OWNED.get(name, ())
    if owner not in owners:
        POLICY_OWNED[name] = owners + (owner,)
    current = os.environ.get(name)
    if current is not None and current.strip() != value:
        _OVERRIDDEN[name] = (current, value)
        logger.warning(
            "[policy] %s=%r из окружения заменён на %r: значение форсирует %s "
            "(контракт качества, менять его в .env бессмысленно)",
            name,
            current,
            value,
            owner,
        )
    os.environ[name] = value
    return value


def force_all(names: "tuple[str, ...] | list[str]", value: str, *, owner: str) -> str:
    """Применить :func:`force` к группе переменных с общим значением."""
    for name in names:
        force(name, value, owner=owner)
    return str(value)


def overridden() -> dict[str, tuple[str, str]]:
    """Снимок подменённых значений: ``{name: (operator_value, enforced)}``."""
    return dict(_OVERRIDDEN)


def override_summary() -> str:
    """Короткая строка для startup-диагностики; пусто, если подмен не было."""
    items = overridden()
    if not items:
        return ""
    return ", ".join(
        f"{name}:{operator!r}->{enforced!r}"
        for name, (operator, enforced) in sorted(items.items())
    )


def reset() -> None:
    """Сбросить реестр подмен (используется тестами, не рабочим кодом)."""
    _OVERRIDDEN.clear()


__all__ = [
    "POLICY_OWNED",
    "force",
    "force_all",
    "override_summary",
    "overridden",
    "reset",
]
