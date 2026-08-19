"""Bearer token authentication dependency."""

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from astra.settings import Settings, get_settings

security = HTTPBearer(auto_error=False)


async def verify_bearer_token(
    credentials: HTTPAuthorizationCredentials = Security(security),
    settings: Settings = Depends(get_settings),
) -> bool:
    """Verifies that the incoming request contains the configured shared-secret Bearer token."""
    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Bearer authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if credentials.credentials != settings.auth_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid Bearer authentication token",
        )

    return True
