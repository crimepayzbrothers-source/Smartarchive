"""Gemeinsame Helfer für die API: JSON, Validierung, Anmeldung."""
from __future__ import annotations

import functools
import json
from typing import Any, Awaitable, Callable, TypeVar

from pydantic import BaseModel, ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ..db import connect
from ..security import COOKIE_NAME, session_user

M = TypeVar("M", bound=BaseModel)


class ApiError(Exception):
    def __init__(self, status: int, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.extra = extra


def ok(data: Any = None, status: int = 200) -> JSONResponse:
    return JSONResponse(data if data is not None else {"ok": True}, status_code=status)


async def body(request: Request, model: type[M]) -> M:
    try:
        raw = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ApiError(400, "Ungültiges JSON") from None
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        field = ".".join(str(p) for p in first.get("loc", []))
        raise ApiError(422, f"Ungültige Eingabe bei „{field}“: {first.get('msg')}") from None


def int_param(request: Request, name: str) -> int | None:
    raw = request.query_params.get(name)
    if raw in (None, "", "null"):
        return None
    try:
        return int(raw)
    except ValueError:
        raise ApiError(400, f"Parameter {name} muss eine Zahl sein") from None


def path_int(request: Request, name: str = "id") -> int:
    return int(request.path_params[name])


Handler = Callable[[Request], Awaitable[Response]]


def endpoint(auth: bool = True) -> Callable[[Callable[..., Any]], Handler]:
    """Dekorator: Datenbankverbindung, Anmeldung, Fehlerbehandlung.

    Die eigentliche (async) Funktion erhält (request, conn, user). Rechenintensive
    Schritte (Export, Seitenvorschau) laufen dort über run_in_threadpool.
    """

    def deco(fn: Callable[..., Any]) -> Handler:
        @functools.wraps(fn)
        async def wrapper(request: Request) -> Response:
            conn = connect()
            try:
                user = session_user(conn, request.cookies.get(COOKIE_NAME))
                if auth and not user:
                    return JSONResponse({"error": "Bitte anmelden."}, status_code=401)
                if request.method not in ("GET", "HEAD") and request.headers.get("x-requested-with") != "SmartArchive":
                    return JSONResponse({"error": "Anfrage abgelehnt (CSRF-Schutz)."}, status_code=403)
                request.state.user = user
                result = await fn(request, conn, user)
                if isinstance(result, Response):
                    return result
                return ok(result)
            except ApiError as exc:
                return JSONResponse({"error": exc.message, **exc.extra}, status_code=exc.status)
            except KeyError:
                return JSONResponse({"error": "Nicht gefunden."}, status_code=404)
            finally:
                conn.close()

        return wrapper

    return deco
