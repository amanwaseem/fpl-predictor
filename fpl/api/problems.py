"""Errors as RFC 9457 problem details: application/problem+json.

Every error the API returns has the same shape, whether it is a missing
gameweek, a malformed query parameter or a route that does not exist.
"""

from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException

MEDIA_TYPE = "application/problem+json"

class Problem(BaseModel):
    type: str = "about:blank"
    title: str
    status: int
    detail: str | None = None
    instance: str | None = None
    # Validation failures only: which parameter, and what was wrong with it.
    errors: list[dict[str, Any]] | None = None


def problem_response(request, status, detail=None, *, errors=None, headers=None):
    body = Problem(title=HTTPStatus(status).phrase, status=status, detail=detail,
                   instance=request.url.path, errors=errors)
    return JSONResponse(body.model_dump(exclude_none=True), status_code=status,
                        media_type=MEDIA_TYPE, headers=headers)


async def http_error(request: Request, exc: HTTPException):
    return problem_response(request, exc.status_code, exc.detail,
                            headers=getattr(exc, "headers", None))


async def validation_error(request: Request, exc: RequestValidationError):
    errors = [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]}
              for e in jsonable_encoder(exc.errors())]
    return problem_response(request, 422, "The request's parameters are not valid.",
                            errors=errors)


async def server_error(request: Request, exc: Exception):
    # Not logged here: Starlette re-raises after this handler, and the server
    # logs the traceback then. Logging it here too would print it twice.
    return problem_response(request, 500)


def install(app: FastAPI):
    app.add_exception_handler(HTTPException, http_error)
    app.add_exception_handler(RequestValidationError, validation_error)
    app.add_exception_handler(Exception, server_error)


def responses(*statuses):
    """OpenAPI entries for problem responses; app.py moves them to problem+json."""
    return {status: {"model": Problem, "description": HTTPStatus(status).phrase}
            for status in statuses}
