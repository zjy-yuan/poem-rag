from fastapi import APIRouter

from app.api.v1 import (
    auth,
    authors,
    categories,
    conversations,
    dynasties,
    health,
    poems,
    search,
    users,
)
from app.api.v1.admin.router import router as admin_router

api_router = APIRouter()
api_router.include_router(health.router, tags=["system"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(users.router, prefix="/users", tags=["users"])
api_router.include_router(poems.router, prefix="/poems", tags=["poems"])
api_router.include_router(authors.router, prefix="/authors", tags=["authors"])
api_router.include_router(dynasties.router, prefix="/dynasties", tags=["dynasties"])
api_router.include_router(categories.router, prefix="/categories", tags=["categories"])
api_router.include_router(search.router, prefix="/search", tags=["search"])
api_router.include_router(
    conversations.router,
    prefix="/conversations",
    tags=["conversations"],
)
api_router.include_router(admin_router, prefix="/admin", tags=["admin"])
