from fastapi import APIRouter

from app.api.v1.admin import authors, categories, dynasties, index_runs, poems

router = APIRouter()
router.include_router(poems.router, prefix="/poems", tags=["admin-poems"])
router.include_router(authors.router, prefix="/authors", tags=["admin-authors"])
router.include_router(dynasties.router, prefix="/dynasties", tags=["admin-dynasties"])
router.include_router(categories.router, prefix="/categories", tags=["admin-categories"])
router.include_router(
    index_runs.router,
    prefix="/index-runs",
    tags=["admin-index-runs"],
)
