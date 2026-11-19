from fastapi import APIRouter

from app.api.v1 import auth, experiments, labs

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(labs.router)
api_router.include_router(experiments.router)
