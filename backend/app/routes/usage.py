"""Usage accounting router: AI credit spend and remaining budget."""
from fastapi import APIRouter

from ..config import get_settings
from ..database import create_tables
from ..guards import investigations_in_use
from ..investigation_cache import cache_stats as investigation_cache_stats
from ..provider import get_cache_stats, get_total_spent, get_usage_summary

router = APIRouter()


@router.get("/")
async def get_usage():
    """Return current AI credit usage and budget status."""
    settings = get_settings()
    await create_tables(settings.db_path)

    total_spent = await get_total_spent(settings.db_path)
    by_model = await get_usage_summary(settings.db_path)
    cache = await get_cache_stats(settings.db_path)
    inv_cache = await investigation_cache_stats(settings.db_path)

    remaining = settings.budget_limit_usd - total_spent
    is_near_limit = remaining <= settings.budget_reserve_usd * 2

    return {
        "total_spent_usd": round(total_spent, 4),
        "budget_limit_usd": settings.budget_limit_usd,
        "budget_reserve_usd": settings.budget_reserve_usd,
        "remaining_usd": round(remaining, 4),
        "is_near_limit": is_near_limit,
        "by_model": by_model,
        # Phase 6: cache + concurrency observability.
        "cache": cache,
        "investigations_in_use": investigations_in_use(),
        "investigation_concurrency_limit": settings.investigation_max_concurrent,
        # Phase 8: investigation-result cache observability.
        "investigation_cache": inv_cache,
    }
