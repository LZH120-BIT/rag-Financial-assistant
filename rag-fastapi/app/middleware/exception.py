import logging
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException


async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(
        content={
            "statusCode": exc.status_code,
            "message": exc.detail,
            "data": [],
            "api": request.url.path,
        },
        status_code=exc.status_code,
    )


async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = exc.errors()
    messages = [e["msg"] for e in errors]
    return JSONResponse(
        content={
            "statusCode": 422,
            "message": messages[0] if messages else "参数校验失败",
            "data": [],
            "api": request.url.path,
        },
        status_code=422,
    )


async def general_exception_handler(request: Request, exc: Exception):
    logging.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        content={
            "statusCode": 500,
            "message": "服务器发生错误",
            "data": [],
            "api": request.url.path,
        },
        status_code=500,
    )