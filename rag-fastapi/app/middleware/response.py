import json
import logging
from fastapi import Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.middleware.base import BaseHTTPMiddleware


class ResponseWrapperMiddleware(BaseHTTPMiddleware):
    """
    等价于 NestJS 的 TransformInterceptor
    统一包装响应为 { statusCode, message, data, api }
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)

        content_type = response.headers.get("content-type", "")
        if "text/" in content_type or "stream" in content_type.lower():
            return response

        body = b""
        async for chunk in response.body_iterator:
            body += chunk

        # 保留原始响应中的 CORS 等 headers
        original_headers = {}
        for key, value in response.headers.items():
            if key.lower().startswith("access-control-") or key.lower() == "vary":
                original_headers[key] = value

        try:
            data = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return Response(content=body, status_code=response.status_code, headers=dict(response.headers))

        if isinstance(data, dict) and "message" in data and "result" in data:
            message = data["message"]
            result = data["result"]
            code = data.get("code", 200)
        else:
            message = "SUCCESS"
            result = data
            code = response.status_code

        wrapped = JSONResponse(
            content={
                "statusCode": code,
                "message": message,
                "data": result,
                "api": request.url.path,
            },
            status_code=code,
        )
        for key, value in original_headers.items():
            wrapped.headers[key] = value
        return wrapped