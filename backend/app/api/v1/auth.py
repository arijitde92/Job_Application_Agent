"""
auth_router.py
--------------
Authentication endpoints: register, login, Google OAuth, and current user profile.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models import User
from backend.schemas import (
    RegisterRequest, LoginRequest, GoogleOAuthRequest,
    TokenResponse, UserResponse,
)
from backend.auth import (
    hash_password, verify_password, create_access_token,
    get_current_user, verify_google_id_token,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(req: RegisterRequest, db: AsyncSession = Depends(get_db)):
    """Register a new user account with email and password."""
    # Check for duplicate email
    existing = await db.execute(select(User).where(User.email == req.email))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        )

    user = User(
        first_name=req.first_name,
        last_name=req.last_name,
        email=req.email,
        password_hash=hash_password(req.password),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    token = create_access_token(user.id, user.email)
    return TokenResponse(access_token=token)


@router.post("/login", response_model=TokenResponse)
async def login(req: LoginRequest, db: AsyncSession = Depends(get_db)):
    """Login with email and password, returns a JWT token."""
    result = await db.execute(select(User).where(User.email == req.email))
    user = result.scalar_one_or_none()

    if not user or not user.password_hash or not verify_password(req.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )

    token = create_access_token(user.id, user.email)
    return TokenResponse(access_token=token)


@router.post("/google", response_model=TokenResponse)
async def google_oauth_login(req: GoogleOAuthRequest, db: AsyncSession = Depends(get_db)):
    """
    Login or register via Google OAuth.
    Verifies the Google ID token server-side, creates a user if first login.
    """
    idinfo = await verify_google_id_token(req.id_token)
    email = idinfo.get("email")
    if not email:
        raise HTTPException(status_code=400, detail="Google token missing email claim.")

    # Find or create user
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if not user:
        # First-time Google OAuth login — create account
        user = User(
            first_name=idinfo.get("given_name", ""),
            last_name=idinfo.get("family_name", ""),
            email=email,
            password_hash="",  # OAuth-only user, no password
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)

    token = create_access_token(user.id, user.email)
    return TokenResponse(access_token=token)


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    """Return the currently authenticated user's profile."""
    return current_user
