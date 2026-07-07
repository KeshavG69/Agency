"""
Configuration constants for the authentication module
"""

import os
from dotenv import load_dotenv

load_dotenv()

# Secret key for JWT - in production, use a secure random key
SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key-change-this-in-production")
ALGORITHM = os.getenv("ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
REFRESH_TOKEN_EXPIRE_DAYS = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7"))

# Cookie Configuration
COOKIE_DOMAIN = os.getenv("COOKIE_DOMAIN", None) or None  # None = current domain
COOKIE_SECURE = os.getenv("ENVIRONMENT", "development") == "production"
# For cross-origin cookies (different subdomains), use "none" with secure=True
COOKIE_SAMESITE = os.getenv("COOKIE_SAMESITE", "none" if COOKIE_SECURE else "lax")
COOKIE_ACCESS_TOKEN_NAME = "access_token"
COOKIE_REFRESH_TOKEN_NAME = "refresh_token"

# Frontend URL — used to build verification / reset / invite links (Collecct runs on :3001 in dev)
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3001")

# Email Configuration
# Preferred: Resend HTTP API (no SMTP auth pain). Set RESEND_API_KEY and
# `EmailService` will route through Resend. SMTP_* vars below remain a
# fallback used only when RESEND_API_KEY is empty (mostly local dev).
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
# NOTE: FROM_EMAIL must be on a domain verified in Resend — override in .env for prod.
FROM_EMAIL = os.getenv("FROM_EMAIL", "noreply@collecct.app")
FROM_NAME = os.getenv("FROM_NAME", "Collecct")
# Support inbox (contact form / reply-to). Defaults to the from-address.
SUPPORT_EMAIL = os.getenv("SUPPORT_EMAIL", FROM_EMAIL)

# Google OAuth Configuration
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")

# Microsoft OAuth Configuration ("Sign in with Microsoft" — a pure identity check, distinct
# from the Outlook/SharePoint DATA connections which go through Composio separately). Reuses
# the same "Collecct" Azure App Registration already set up for SharePoint — just needs an
# ADDITIONAL Redirect URI added there (MICROSOFT_REDIRECT_URI below), not a new app.
MICROSOFT_CLIENT_ID = os.getenv("MICROSOFT_CLIENT_ID", "")
MICROSOFT_CLIENT_SECRET = os.getenv("MICROSOFT_CLIENT_SECRET", "")
# "common" = any Microsoft work/school/personal account (matches the app's multitenant setting).
MICROSOFT_TENANT = os.getenv("MICROSOFT_TENANT", "common")
MICROSOFT_AUTHORITY = f"https://login.microsoftonline.com/{MICROSOFT_TENANT}"
# Must be registered as a Redirect URI (Web platform) on the Azure app — the frontend page that
# receives Microsoft's redirect-back with ?code=&state=.
MICROSOFT_REDIRECT_URI = os.getenv("MICROSOFT_REDIRECT_URI", f"{FRONTEND_URL}/auth/microsoft-callback")

# Terms and Conditions Configuration
CURRENT_TERMS_VERSION = "1.0.0"
