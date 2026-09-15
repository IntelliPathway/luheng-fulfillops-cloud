from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from .security import RequestContext, request_context


def get_db(request: Request):
    with request.app.state.Session() as db:
        yield db


Context = Annotated[RequestContext, Depends(request_context)]
Database = Annotated[Session, Depends(get_db)]
