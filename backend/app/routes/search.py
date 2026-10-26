"""Search router: search for objects across all indexed recordings."""
import logging
from fastapi import APIRouter, HTTPException, Query

from ..config import get_settings
from ..database import create_tables
from ..models import SearchResult
from ..search import search_observations

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/", response_model=SearchResult)
async def search(
    q: str = Query(..., description="Natural language search query", min_length=1, max_length=500),
):
    """Search for an object across all indexed recordings."""
    settings = get_settings()
    await create_tables(settings.db_path)
    result = await search_observations(query=q, db_path=settings.db_path)
    return result
