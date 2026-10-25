"""Public Service dependency shared by route groups."""
from typing import Annotated

from fastapi import Depends, Request

from tentacles.service import TentaclesService


def service(request: Request) -> TentaclesService:
    return request.app.state.service


Service = Annotated[TentaclesService, Depends(service)]
