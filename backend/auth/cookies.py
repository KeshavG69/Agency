"""
Cookie management utilities for authentication
"""

from fastapi import Response, Request
from typing import Optional
from .config import (
    COOKIE_ACCESS_TOKEN_NAME,
    COOKIE_REFRESH_TOKEN_NAME,
    COOKIE_SECURE,
    COOKIE_SAMESITE,
    COOKIE_DOMAIN,
    ACCESS_TOKEN_EXPIRE_MINUTES,
    REFRESH_TOKEN_EXPIRE_DAYS
)


def set_access_token_cookie(response: Response, token: str) -> None:
    """
    Set access token as HttpOnly cookie

    Args:
        response: FastAPI Response object
        token: JWT access token string
    """
    response.set_cookie(
        key=COOKIE_ACCESS_TOKEN_NAME,
        value=token,
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,  # Convert to seconds
        httponly=True,  # Prevents JavaScript access (XSS protection)
        secure=COOKIE_SECURE,  # HTTPS only in production
        samesite=COOKIE_SAMESITE,  # CSRF protection
        domain=COOKIE_DOMAIN,
        path="/"
    )


def set_refresh_token_cookie(response: Response, token: str) -> None:
    """
    Set refresh token as HttpOnly cookie

    Args:
        response: FastAPI Response object
        token: JWT refresh token string
    """
    response.set_cookie(
        key=COOKIE_REFRESH_TOKEN_NAME,
        value=token,
        max_age=REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,  # Convert to seconds
        httponly=True,  # Prevents JavaScript access (XSS protection)
        secure=COOKIE_SECURE,  # HTTPS only in production
        samesite=COOKIE_SAMESITE,  # CSRF protection
        domain=COOKIE_DOMAIN,
        path="/"
    )


def clear_auth_cookies(response: Response) -> None:
    """
    Clear both access and refresh token cookies

    Args:
        response: FastAPI Response object
    """
    response.delete_cookie(
        key=COOKIE_ACCESS_TOKEN_NAME,
        domain=COOKIE_DOMAIN,
        path="/"
    )
    response.delete_cookie(
        key=COOKIE_REFRESH_TOKEN_NAME,
        domain=COOKIE_DOMAIN,
        path="/"
    )


def get_token_from_cookie(request: Request, cookie_name: str) -> Optional[str]:
    """
    Extract token from cookie

    Args:
        request: FastAPI Request object
        cookie_name: Name of the cookie to retrieve

    Returns:
        Token string if found, None otherwise
    """
    return request.cookies.get(cookie_name)
