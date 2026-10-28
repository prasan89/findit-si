"""Model-provider resolution for Meta and Baseten chat-completion endpoints.

Both providers speak the OpenAI-compatible ``POST /chat/completions`` API, so
routing is a pure configuration concern: (provider, base_url, api_key, model).
Model selection rule (documented, no magic): the *meta* paths use
VISION_MODEL / INVESTIGATION_MODEL; the *baseten* paths always use
BASETEN_MODEL. Unknown provider names fail fast with ValueError.

Missing keys never raise here: callers fall back to the existing deterministic
mock path (agents report MOCKED, vision reports mock-v1), exactly as the
Meta path has always behaved without a key.
"""
import logging
import os
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

PROVIDER_META = "meta"
PROVIDER_BASETEN = "baseten"
KNOWN_PROVIDERS = frozenset({PROVIDER_META, PROVIDER_BASETEN})

DEFAULT_META_BASE_URL = "https://api.meta.ai/v1"


@dataclass(frozen=True)
class ProviderSpec:
    provider: str
    base_url: str
    api_key: str
    model: str


def _normalize_provider(value: Optional[str], setting_name: str) -> str:
    provider = (value or PROVIDER_META).strip().lower()
    if provider not in KNOWN_PROVIDERS:
        raise ValueError(
            f"Unknown provider {value!r} for {setting_name}; "
            f"expected one of {sorted(KNOWN_PROVIDERS)}"
        )
    return provider


def _meta_key() -> str:
    return (
        os.environ.get("MODEL_API_KEY")
        or os.environ.get("META_API_KEY")
        or ""
    )


def _meta_base_url(settings) -> str:
    return (
        os.environ.get("META_API_BASE_URL")
        or getattr(settings, "meta_api_base_url", "")
        or DEFAULT_META_BASE_URL
    )


def _baseten_key(settings) -> str:
    return (
        os.environ.get("BASETEN_API_KEY")
        or getattr(settings, "baseten_api_key", "")
        or ""
    )


def resolve_vision_spec(settings, provider: Optional[str] = None) -> ProviderSpec:
    """Resolve the frame-indexing provider spec from settings (+ overrides)."""
    name = _normalize_provider(
        provider if provider is not None else settings.vision_provider,
        "VISION_PROVIDER",
    )
    if name == PROVIDER_BASETEN:
        return ProviderSpec(
            provider=name,
            base_url=settings.baseten_base_url,
            api_key=_baseten_key(settings),
            model=settings.baseten_model,
        )
    return ProviderSpec(
        provider=name,
        base_url=_meta_base_url(settings),
        api_key=_meta_key(),
        model=settings.vision_model,
    )


def resolve_text_spec(
    settings,
    provider: Optional[str] = None,
    model: Optional[str] = None,
) -> ProviderSpec:
    """Resolve the investigation-agent LLM spec.

    An explicit model always wins; otherwise the provider default applies
    (INVESTIGATION_MODEL for meta, BASETEN_MODEL for baseten).
    """
    name = _normalize_provider(
        provider if provider is not None else settings.investigation_provider,
        "INVESTIGATION_PROVIDER",
    )
    if name == PROVIDER_BASETEN:
        resolved_model = model or settings.baseten_model
        return ProviderSpec(
            provider=name,
            base_url=settings.baseten_base_url,
            api_key=_baseten_key(settings),
            model=resolved_model,
        )
    return ProviderSpec(
        provider=name,
        base_url=_meta_base_url(settings),
        api_key=_meta_key(),
        model=model or settings.investigation_model,
    )


def validate_provider_config(settings) -> list[str]:
    """Return human-readable config problems (empty == OK). Logs warnings.

    Called at startup: a selected provider without its key is reported but
    does not crash the app — that path serves deterministic mocks instead.
    """
    problems = []
    for setting_name, resolver in (
        ("VISION_PROVIDER", resolve_vision_spec),
        ("INVESTIGATION_PROVIDER", resolve_text_spec),
    ):
        try:
            spec = resolver(settings)
        except ValueError as exc:
            problems.append(str(exc))
            continue
        if not spec.api_key:
            problems.append(
                f"{setting_name}={spec.provider} but no API key is configured; "
                "that path will use deterministic mocks"
            )
    for problem in problems:
        logger.warning("Provider config: %s", problem)
    return problems
