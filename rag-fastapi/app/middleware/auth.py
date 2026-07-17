from typing import Optional
from fastapi import Request, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from app.config import settings

security = HTTPBearer()


def extract_token_from_header(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    parts = authorization.split(" ")
    if len(parts) == 2 and parts[0] == "Bearer":
        return parts[1]
    return None


async def get_current_user(
    request: Request, credentials: HTTPAuthorizationCredentials = Depends(security)
) -> str:
    token = credentials.credentials
    if not token:
        raise HTTPException(status_code=401, detail="未携带token")
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=["HS256"])
        user_id = payload.get("token")
        if user_id is None:
            raise HTTPException(status_code=401, detail="token无效")
        request.state.user_id = user_id
        return user_id
    except JWTError:
        raise HTTPException(status_code=401, detail="token无效")