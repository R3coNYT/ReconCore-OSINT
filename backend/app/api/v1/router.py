"""API v1 route aggregation."""
from fastapi import APIRouter

from app.api.v1.routes import (
    auth,
    evidence,
    graph,
    investigations,
    persons,
    plugins,
    search,
    users,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(investigations.router)
api_router.include_router(persons.router)
api_router.include_router(evidence.router)
api_router.include_router(search.router)
api_router.include_router(plugins.router)
api_router.include_router(graph.router)
