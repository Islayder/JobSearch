from fastapi import APIRouter

from radar_vagas.web.routes import (
    agenda,
    applications,
    dashboard,
    gmail,
    jobs,
    profiles,
    settings,
    sources,
)

router = APIRouter()
router.include_router(dashboard.router)
router.include_router(jobs.router)
router.include_router(applications.router)
router.include_router(agenda.router)
router.include_router(profiles.router)
router.include_router(sources.router)
router.include_router(gmail.router)
router.include_router(settings.router)

__all__ = ["router"]
