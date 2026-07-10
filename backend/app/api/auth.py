"""Authentication endpoints -- login, register, current user, OIDC SSO."""

from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from jose import jwt, JWTError, jwk as jose_jwk
from passlib.context import CryptContext
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.database import get_db
from app.config import settings
from app.models.user import User, Role
from app.schemas.auth import LoginRequest, TokenResponse, UserCreate, UserResponse
from app.services.audit import record_audit
from app.services.permissions import require_permission
from app.rate_limit import limiter

router = APIRouter(prefix="/auth", tags=["auth"])
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer()


def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expiry_minutes)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
) -> User:
    """FastAPI dependency to extract and validate the current user from JWT."""
    token = credentials.credentials
    try:
        payload = jwt.decode(
            token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
        )
        user_id: str = payload.get("sub")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid token")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

    user = db.query(User).filter(User.id == user_id).first()
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")
    return user


@router.post("/login", response_model=TokenResponse)
@limiter.limit("10/minute")
async def login(request: Request, body: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == body.username).first()
    if not user or not user.hashed_password or not pwd_context.verify(body.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )

    token = create_access_token({"sub": user.id, "role": user.role.name})
    record_audit(
        db,
        action="auth.login",
        entity_type="user",
        entity_id=user.id,
        actor_id=user.id,
        actor_username=user.username,
        commit=True,
    )
    return TokenResponse(
        access_token=token,
        user_id=user.id,
        username=user.username,
        role=user.role.name,
    )


@router.post("/register", response_model=UserResponse)
async def register(
    request: UserCreate,
    db: Session = Depends(get_db),
):
    """Register a new user.

    In production (or when ALLOW_OPEN_REGISTRATION=false), open registration is disabled.
    Self-registration is limited to analyst / input_provider. Admins use /auth/admin/users.
    """
    open_reg = settings.allow_open_registration and not settings.is_production

    if not open_reg:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Open registration is disabled. Ask an administrator to create your account.",
        )

    existing = db.query(User).filter(
        (User.email == request.email) | (User.username == request.username)
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="User already exists")

    allowed_self_roles = {"analyst", "input_provider"}
    role_name = request.role_name or "analyst"
    if role_name not in allowed_self_roles:
        raise HTTPException(
            status_code=403,
            detail=f"Self-registration limited to roles: {sorted(allowed_self_roles)}",
        )

    role = db.query(Role).filter(Role.name == role_name).first()
    if not role:
        raise HTTPException(status_code=400, detail=f"Role '{role_name}' not found")

    user = User(
        email=request.email,
        username=request.username,
        hashed_password=pwd_context.hash(request.password),
        full_name=request.full_name,
        business_unit=request.business_unit,
        role_id=role.id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    record_audit(
        db,
        action="auth.register",
        entity_type="user",
        entity_id=user.id,
        actor_username=user.username,
        details={"role": role_name},
        commit=True,
    )

    return UserResponse(
        id=user.id,
        email=user.email,
        username=user.username,
        full_name=user.full_name,
        business_unit=user.business_unit,
        role_name=user.role.name,
        is_active=user.is_active,
    )


@router.post("/admin/users", response_model=UserResponse)
async def admin_create_user(
    request: UserCreate,
    current_user: User = Depends(require_permission("admin")),
    db: Session = Depends(get_db),
):
    """Admin-only user provisioning (any role)."""
    existing = db.query(User).filter(
        (User.email == request.email) | (User.username == request.username)
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="User already exists")

    role = db.query(Role).filter(Role.name == request.role_name).first()
    if not role:
        raise HTTPException(status_code=400, detail=f"Role '{request.role_name}' not found")

    user = User(
        email=request.email,
        username=request.username,
        hashed_password=pwd_context.hash(request.password),
        full_name=request.full_name,
        business_unit=request.business_unit,
        role_id=role.id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    record_audit(
        db,
        action="admin.create_user",
        entity_type="user",
        entity_id=user.id,
        actor_id=current_user.id,
        actor_username=current_user.username,
        details={"role": request.role_name, "username": user.username},
        commit=True,
    )

    return UserResponse(
        id=user.id,
        email=user.email,
        username=user.username,
        full_name=user.full_name,
        business_unit=user.business_unit,
        role_name=user.role.name,
        is_active=user.is_active,
    )


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    return UserResponse(
        id=current_user.id,
        email=current_user.email,
        username=current_user.username,
        full_name=current_user.full_name,
        business_unit=current_user.business_unit,
        role_name=current_user.role.name,
        is_active=current_user.is_active,
    )


@router.get("/oidc/login")
async def oidc_login():
    """Start OIDC authorization-code flow with state, nonce, and PKCE (S256)."""
    if not settings.oidc_enabled:
        raise HTTPException(status_code=404, detail="OIDC SSO is not enabled")
    if not settings.oidc_issuer or not settings.oidc_client_id:
        raise HTTPException(status_code=500, detail="OIDC is enabled but not configured")

    from app.services.oidc_store import create_oidc_pending, pkce_challenge

    pending = create_oidc_pending()
    authorize_url = settings.oidc_issuer.rstrip("/") + "/protocol/openid-connect/auth"
    params = {
        "client_id": settings.oidc_client_id,
        "response_type": "code",
        "scope": settings.oidc_scopes,
        "redirect_uri": settings.oidc_redirect_uri,
        "state": pending.state,
        "nonce": pending.nonce,
        "code_challenge": pkce_challenge(pending.code_verifier),
        "code_challenge_method": "S256",
    }
    return RedirectResponse(f"{authorize_url}?{urlencode(params)}")


async def _verify_oidc_id_token(client, id_token: str, expected_nonce: str) -> dict:
    """Verify id_token signature against the IdP JWKS and return claims."""
    issuer = settings.oidc_issuer.rstrip("/")
    # Discover JWKS URI (OIDC + Keycloak-style fallbacks)
    jwks_uri = None
    for path in ("/.well-known/openid-configuration", "/.well-known/openid-configuration/"):
        try:
            conf = await client.get(f"{issuer}{path}")
            if conf.status_code < 400:
                jwks_uri = conf.json().get("jwks_uri")
                if jwks_uri:
                    break
        except Exception:
            continue
    if not jwks_uri:
        jwks_uri = f"{issuer}/protocol/openid-connect/certs"

    jwks_resp = await client.get(jwks_uri)
    if jwks_resp.status_code >= 400:
        raise ValueError("Unable to fetch IdP JWKS")
    jwks = jwks_resp.json()

    header = jwt.get_unverified_header(id_token)
    kid = header.get("kid")
    key = None
    for jwk in jwks.get("keys", []):
        if kid is None or jwk.get("kid") == kid:
            key = jwk
            break
    if key is None:
        raise ValueError("No matching JWK for id_token")

    key_obj = jose_jwk.construct(key)
    claims = jwt.decode(
        id_token,
        key_obj,
        algorithms=[header.get("alg", "RS256")],
        audience=settings.oidc_client_id,
        options={"verify_at_hash": False},
    )
    if claims.get("iss") and not str(claims["iss"]).rstrip("/").startswith(issuer):
        # Allow issuer with/without trailing slash
        if str(claims["iss"]).rstrip("/") != issuer:
            raise ValueError("id_token issuer mismatch")
    if expected_nonce and claims.get("nonce") and claims["nonce"] != expected_nonce:
        raise ValueError("nonce mismatch")
    return claims


@router.get("/oidc/callback")
async def oidc_callback(
    code: str,
    state: str | None = None,
    db: Session = Depends(get_db),
):
    """Exchange OIDC code for tokens; redirect with a one-time app code (not JWT)."""
    if not settings.oidc_enabled:
        raise HTTPException(status_code=404, detail="OIDC SSO is not enabled")

    import httpx
    from app.services.oidc_store import issue_one_time_code, pop_oidc_pending

    pending = pop_oidc_pending(state)
    if not pending:
        raise HTTPException(status_code=400, detail="Invalid or expired OIDC state")

    token_url = settings.oidc_issuer.rstrip("/") + "/protocol/openid-connect/token"
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            discovery = await client.get(
                settings.oidc_issuer.rstrip("/") + "/.well-known/openid-configuration"
            )
            if discovery.status_code == 200:
                disc = discovery.json()
                token_url = disc.get("token_endpoint", token_url)
                userinfo_url = disc.get("userinfo_endpoint")
            else:
                userinfo_url = settings.oidc_issuer.rstrip("/") + "/protocol/openid-connect/userinfo"
        except Exception:
            userinfo_url = settings.oidc_issuer.rstrip("/") + "/protocol/openid-connect/userinfo"

        token_resp = await client.post(
            token_url,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.oidc_redirect_uri,
                "client_id": settings.oidc_client_id,
                "client_secret": settings.oidc_client_secret,
                "code_verifier": pending.code_verifier,
            },
        )
        if token_resp.status_code >= 400:
            raise HTTPException(status_code=401, detail="OIDC token exchange failed")
        tokens = token_resp.json()

        # Validate id_token signature via IdP JWKS + nonce
        id_token = tokens.get("id_token")
        if id_token:
            try:
                claims = await _verify_oidc_id_token(client, id_token, pending.nonce)
                if claims.get("nonce") and claims.get("nonce") != pending.nonce:
                    raise HTTPException(status_code=401, detail="OIDC nonce mismatch")
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(status_code=401, detail=f"OIDC id_token invalid: {e}") from e

        access = tokens.get("access_token")
        userinfo = await client.get(
            userinfo_url, headers={"Authorization": f"Bearer {access}"}
        )
        if userinfo.status_code >= 400:
            raise HTTPException(status_code=401, detail="OIDC userinfo failed")
        info = userinfo.json()

    email = info.get("email") or info.get("preferred_username")
    username = info.get("preferred_username") or (email.split("@")[0] if email else None)
    if not email or not username:
        raise HTTPException(status_code=400, detail="OIDC profile missing email/username")

    user = db.query(User).filter((User.email == email) | (User.username == username)).first()
    if not user:
        role = db.query(Role).filter(Role.name == "analyst").first()
        user = User(
            email=email,
            username=username,
            hashed_password=pwd_context.hash(f"oidc:{username}"),
            full_name=info.get("name") or username,
            role_id=role.id if role else None,
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    record_audit(
        db,
        action="auth.oidc_login",
        entity_type="user",
        entity_id=user.id,
        actor_id=user.id,
        actor_username=user.username,
        commit=True,
    )
    one_time = issue_one_time_code(user.id)
    frontend = settings.cors_origin_list[0] if settings.cors_origin_list else "http://localhost:5173"
    return RedirectResponse(f"{frontend}/login?sso_code={one_time}")


class SSOExchangeRequest(BaseModel):
    code: str


@router.post("/oidc/exchange", response_model=TokenResponse)
async def oidc_exchange(body: SSOExchangeRequest, db: Session = Depends(get_db)):
    """Exchange a one-time SSO code (from callback redirect) for an app JWT."""
    from app.services.oidc_store import redeem_one_time_code

    user_id = redeem_one_time_code(body.code)
    if not user_id:
        raise HTTPException(status_code=400, detail="Invalid or expired SSO code")
    user = db.query(User).filter(User.id == user_id, User.is_active == True).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found or inactive")
    app_token = create_access_token(
        {"sub": user.id, "role": user.role.name if user.role else "analyst"}
    )
    return TokenResponse(
        access_token=app_token,
        user_id=user.id,
        username=user.username,
        role=user.role.name if user.role else "analyst",
    )


@router.get("/sso/status")
async def sso_status():
    return {
        "oidc_enabled": settings.oidc_enabled,
        "issuer": settings.oidc_issuer if settings.oidc_enabled else None,
        "login_url": "/api/auth/oidc/login" if settings.oidc_enabled else None,
    }
