"""Compatibility exports for core-owned Gemini quota-domain contracts."""

from core.gemini_quota_domains import (
    GEMINI_QUOTA_DOMAIN_ENVS,
    ProjectQuotaDomainTracker,
    _exception_status_code,
    is_project_scoped_quota_error,
    is_quota_error,
    key_domain_entries,
    normalize_quota_domain_label,
    quota_domains_for_keys,
)

__all__ = [
    "GEMINI_QUOTA_DOMAIN_ENVS",
    "ProjectQuotaDomainTracker",
    "_exception_status_code",
    "is_project_scoped_quota_error",
    "is_quota_error",
    "key_domain_entries",
    "normalize_quota_domain_label",
    "quota_domains_for_keys",
]
