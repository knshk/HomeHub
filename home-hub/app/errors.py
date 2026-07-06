"""Shared error type + JSON error envelope helpers."""
from fastapi import Request
from fastapi.responses import JSONResponse


class HubError(Exception):
    """Raise anywhere to return a JSON {"error":{message,code}} envelope."""

    def __init__(self, status: int, message: str, code: str = "error"):
        super().__init__(message)
        self.status = status
        self.message = message
        self.code = code


def error_body(message: str, code: str = "error") -> dict:
    return {"error": {"message": message, "code": code}}


def hub_error_handler(request: Request, exc: HubError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content=error_body(exc.message, exc.code))
