from fastapi import APIRouter

from app.api.v1.endpoints import analysis, auth, broker, claude, options, portfolio, snapshots, strategy, users

api_router = APIRouter()

api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(users.router, prefix="/users", tags=["users"])
api_router.include_router(broker.router, prefix="/broker", tags=["broker"])
api_router.include_router(portfolio.router, prefix="/portfolio", tags=["portfolio"])
api_router.include_router(snapshots.router, prefix="/snapshots", tags=["snapshots"])
api_router.include_router(claude.router, prefix="/claude", tags=["claude"])
api_router.include_router(analysis.router, prefix="/analysis", tags=["analysis"])
api_router.include_router(options.router, prefix="/options", tags=["options"])
api_router.include_router(strategy.router, prefix="/strategy", tags=["strategy"])
