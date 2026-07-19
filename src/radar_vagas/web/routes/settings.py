from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import RedirectResponse

from radar_vagas.web.routes.common import redirect

router = APIRouter(prefix="/settings")


@router.get("")
def settings() -> RedirectResponse:
    return redirect("/profile")
