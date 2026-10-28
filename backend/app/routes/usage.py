"""Usage accounting router: AI credit spend and remaining budget."""
from fastapi import APIRouter

from ..config import get_settings
from ..database import create_tables
from ..provider import get_total_spent, get_usage_summary

router = APIRouter()


@router.get("/")
async def get_usage():
    """Return current AI credit usage and budget status."""
    settings = get_settings()
    await create_tables(settings.db_path)

    total_spent = await get_total_spent(settings.db_path)
    by_model = await get_usage_summary(settings.db_path)

    remaining = settings.budget_limit_usd - total_spent
    is_near_limit = remaining <= settings.budget_reserve_usd * 2

    return {
        "total_spent_usd": round(total_spent, 4),
        "budget_limit_usd": settings.budget_limit_usd,
        "budget_reserve_usd": settings.budget_reserve_usd,
        "remaining_usd": round(remaining, 4),
        "is_near_limit": is_near_limit,
        "by_model": by_model,
    }
