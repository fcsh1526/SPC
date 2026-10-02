from __future__ import annotations

from fastapi.responses import JSONResponse


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, **params):
        self.status, self.code, self.message, self.params = status, code, message, params


def error_response(status: int, code: str, message: str, params: dict | None = None) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message, "params": params or {}}})
