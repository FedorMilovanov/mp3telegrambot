#!/usr/bin/env python3
"""Shared Gemini transport, client rotation, and transient retry routing."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from core import gemini_quota_domains as quota_domains
from core.globals import (
    GEMINI_CLIENT_QUOTA_DOMAINS,
    GEMINI_CLIENTS,
    is_overload_error,
    is_quota_error,
)


def _quota_domains_for_client_list(client_list: list[Any]) -> list[str]:
    """Return privacy-safe domain metadata for known global client objects only."""
    domain_by_client_id = {id(client): domain for client, domain in zip(GEMINI_CLIENTS, GEMINI_CLIENT_QUOTA_DOMAINS)}
    return [domain_by_client_id.get(id(client), "") for client in client_list]


_current_client_idx = 0


async def gemini_generate(
    client_list: list[Any],
    fn: Callable[[Any], Awaitable[Any]],
    model_name: str = "",
    response_validator: Callable[[Any], object] | None = None,
) -> Any:
    """Run one Gemini operation with one global transient budget across keys.

    Quota/429 rotates immediately. If an explicitly labeled credential returns a
    project-scoped 429, later known credentials with the same local opaque label
    are skipped for this call only. Generic 429 remains conservative and rotates
    normally. Overload and transport timeout may reuse one client once, then
    rotate, while the global budget remains initial + at most two retries. Only
    confirmed overload publishes the overload circuit. An optional caller
    response validator may reject a transport-successful response; that consumes
    the current global attempt and rotates without marking any quota domain.
    """
    global _current_client_idx  # noqa: PLW0603 -- round-robin start index is shared across router calls
    from services import gemini_capacity_control as capacity_control

    last_err: Exception | None = None
    if not client_list:
        raise RuntimeError("No Gemini clients available")

    budget = capacity_control.GeminiRetryBudget()
    client_quota_domains = _quota_domains_for_client_list(client_list)
    exhausted_project_quota_domains: set[str] = set()
    same_project_domain_skipped = 0
    start_idx = _current_client_idx
    for i in range(len(client_list)):
        if budget.exhausted:
            break
        idx = (start_idx + i) % len(client_list)
        quota_domain = client_quota_domains[idx]
        if quota_domain and quota_domain in exhausted_project_quota_domains:
            same_project_domain_skipped += 1
            _current_client_idx = (idx + 1) % len(client_list)
            logging.getLogger(__name__).info(
                "Gemini client %s skipped after project-scoped quota on same explicit "
                "local domain; label omitted; skipped=%s",
                idx,
                same_project_domain_skipped,
            )
            continue

        client = client_list[idx]
        _current_client_idx = (idx + 1) % len(client_list)
        same_client_transients = 0

        while not budget.exhausted:
            budget.claim()
            try:
                # Обычная функция вместо lambda с дефолтным аргументом: у
                # lambda mypy не выводит тип параметра, а тут он очевиден.
                def _bound_call(_client: Any = client) -> Awaitable[Any]:
                    return fn(_client)

                result = await capacity_control.run_heavy_gemini_call(_bound_call)
                if response_validator is not None and not bool(response_validator(result)):
                    last_err = RuntimeError("Gemini response rejected by caller validator")
                    break
                return result
            except Exception as e:
                if is_quota_error(e):
                    project_scoped = bool(quota_domain and quota_domains.is_project_scoped_quota_error(e))
                    if project_scoped:
                        exhausted_project_quota_domains.add(quota_domain)
                    logging.getLogger(__name__).warning(
                        "Gemini квота/429 на клиенте %s; project_scoped=%s; "
                        "вращаю клиент в пределах общего budget %s/%s",
                        idx,
                        project_scoped,
                        budget.used,
                        budget.limit,
                    )
                    last_err = e
                    break

                overloaded = is_overload_error(e)
                timed_out = capacity_control.is_timeout_error(e)
                if overloaded or timed_out:
                    last_err = e
                    same_client_transients += 1
                    delay = capacity_control.transient_retry_delay(budget.used)
                    if overloaded:
                        capacity_control.note_overload(delay)
                    kind = "500/503 overload" if overloaded else "timeout"
                    logging.getLogger(__name__).warning(
                        "Gemini transient %s, global attempt %s/%s; backoff %.1fс",
                        kind,
                        budget.used,
                        budget.limit,
                        delay,
                    )
                    if budget.exhausted:
                        break
                    # One retry may reuse the same client; a second transient
                    # moves the final remaining attempt to another configured
                    # client. Key count can never expand the global budget.
                    if same_client_transients == 1:
                        await asyncio.sleep(delay)
                        continue
                    break
                raise

    raise last_err or RuntimeError("Gemini transient retry budget exhausted or no configured client succeeded")


__all__ = ["gemini_generate"]
