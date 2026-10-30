"""Authentication endpoints -- login, register, current user, OIDC SSO."""

import secrets
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
import jwt
from passlib.context import CryptContext
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.database import get_db
from app.config import settings
from app.models.business_unit import BusinessUnit
from app.models.company import Company
from app.models.user import User, Role
from app.schemas.auth import (
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    SwitchCompanyRequest,
    TokenResponse,
    UserCreate,
    UserResponse,
)
from app.services.audit import record_audit
from app.services.company_membership import ensure_active_membership, list_memberships, switch_active_company
from app.services.permissions import require_permission
from app.services.tenant_scope import push_tenant_scope, scope_for_user
from app.services.token_store import (
    create_access_token as _mint_access,
    issue_refresh_token,
    is_jti_revoked,
    revoke_access_from_bearer,
    revoke_refresh_token,
    rotate_refresh_token,
)
from app.rate_limit import limiter

router = APIRouter(prefix="/auth", tags=["auth"])
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer()
security_optional = HTTPBearer(auto_error=False)


def create_access_token(data: dict) -> str:
    """Backward-compatible helper — returns access JWT only."""
    token, _jti, _exp = _mint_access(data)
    return token


def _token_pair(db: Session, user: User) -> TokenResponse:
    access, _jti, _exp = _mint_access(
        {"sub": user.id, "role": user.role.name if user.role else "analyst"}
    )
    refresh = issue_refresh_token(db, user.id)
    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        user_id=user.id,
        username=user.username,
        role=user.role.name if user.role else "analyst",
    )


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
) -> User:
    """FastAPI dependency to extract and validate the current user from JWT.

    Deliberately async (not sync): a sync dependency runs in a worker thread
    via anyio.to_thread, which gets a COPY of the calling context -- a
    contextvar set inside it (push_tenant_scope, below) never propagates
    back to the route handler that runs after it. Being async keeps this in
    the same task/context as everything downstream. The body is otherwise
    synchronous DB access, same as the rest of this module.
    """
    token = credentials.credentials
    try:
        payload = jwt.decode(
            token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
        )
        user_id: str | None = payload.get("sub")
        jti = payload.get("jti")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid token")
        if is_jti_revoked(db, jti):
            raise HTTPException(status_code=401, detail="Token revoked")
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

    user = db.query(User).filter(User.id == user_id).first()
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")
    # Fail-closed session-level tenant scoping for the rest of this request
    # (chat included -- it resolves through this same dependency). See
    # app/services/tenant_scope.py. Each request runs in its own asyncio
    # Task, so this never leaks into another request.
    push_tenant_scope(scope_for_user(user))
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

    record_audit(
        db,
        action="auth.login",
        entity_type="user",
        entity_id=user.id,
        actor_id=user.id,
        actor_username=user.username,
        commit=True,
    )
    return _token_pair(db, user)


@router.post("/refresh", response_model=TokenResponse)
@limiter.limit("30/minute")
async def refresh_tokens(
    request: Request,
    body: RefreshRequest,
    db: Session = Depends(get_db),
):
    """Rotate refresh token and issue a new access token."""
    _ = request
    try:
        new_refresh, user_id = rotate_refresh_token(db, body.refresh_token)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e)) from e

    user = db.query(User).filter(User.id == user_id, User.is_active == True).first()  # noqa: E712
    if not user:
        raise HTTPException(status_code=401, detail="User not found or inactive")

    access, _jti, _exp = _mint_access(
        {"sub": user.id, "role": user.role.name if user.role else "analyst"}
    )
    return TokenResponse(
        access_token=access,
        refresh_token=new_refresh,
        user_id=user.id,
        username=user.username,
        role=user.role.name if user.role else "analyst",
    )


@router.post("/logout")
@limiter.limit("30/minute")
async def logout(
    request: Request,
    body: LogoutRequest | None = None,
    credentials: HTTPAuthorizationCredentials | None = Depends(security_optional),
    db: Session = Depends(get_db),
):
    """Revoke the current access token (jti) and refresh-token family."""
    _ = request
    access = credentials.credentials if credentials else None
    revoke_access_from_bearer(db, access)
    refresh = body.refresh_token if body else None
    revoke_refresh_token(db, refresh)
    return {"ok": True}


@router.post("/register", response_model=UserResponse)
@limiter.limit("10/minute")
async def register(
    request: Request,
    body: UserCreate,
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
        (User.email == body.email) | (User.username == body.username)
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="User already exists")

    allowed_self_roles = {"analyst", "input_provider"}
    role_name = body.role_name or "analyst"
    if role_name not in allowed_self_roles:
        raise HTTPException(
            status_code=403,
            detail=f"Self-registration limited to roles: {sorted(allowed_self_roles)}",
        )

    role = db.query(Role).filter(Role.name == role_name).first()
    if not role:
        raise HTTPException(status_code=400, detail=f"Role '{role_name}' not found")

    from app.services.business_units import get_or_create_business_unit

    # Self-registration may start a new company but never join an existing one:
    # typing a company's name is not proof of membership. Existing companies are
    # joined only through an admin (/auth/admin/users or company memberships).
    if body.business_unit and (
        db.query(BusinessUnit).filter(BusinessUnit.name == body.business_unit).first()
        or db.query(Company).filter(Company.name == body.business_unit).first()
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="That company already exists. Ask its administrator to add you.",
        )

    bu = get_or_create_business_unit(db, body.business_unit)
    user = User(
        email=body.email,
        username=body.username,
        hashed_password=pwd_context.hash(body.password),
        full_name=body.full_name,
        business_unit=body.business_unit,
        business_unit_id=bu.id if bu else None,
        role_id=role.id,
    )
    db.add(user)
    db.flush()
    ensure_active_membership(db, user)
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

    return UserResponse.model_validate(user)


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

    from app.services.business_units import get_or_create_business_unit

    bu = get_or_create_business_unit(db, request.business_unit)
    user = User(
        email=request.email,
        username=request.username,
        hashed_password=pwd_context.hash(request.password),
        full_name=request.full_name,
        business_unit=request.business_unit,
        business_unit_id=bu.id if bu else None,
        role_id=role.id,
    )
    db.add(user)
    db.flush()
    ensure_active_membership(db, user)
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

    return UserResponse.model_validate(user)


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    resp = UserResponse.model_validate(current_user)
    resp.companies = list_memberships(db, current_user)
    return resp


@router.post("/switch-company", response_model=UserResponse)
async def switch_company(
    body: SwitchCompanyRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Switch the caller's active company/role to one they hold a membership
    for. A plain DB write -- every permission check reads business_unit_id/
    role live off the User row, so this takes effect on the very next
    request with no re-login needed."""
    try:
        updated = switch_active_company(db, current_user, body.company_id)
    except ValueError:
        raise HTTPException(404, "Company not found")
    resp = UserResponse.model_validate(updated)
    resp.companies = list_memberships(db, updated)
    return resp


@router.get("/oidc/login")
@limiter.limit("20/minute")
async def oidc_login(request: Request):
    """Start OIDC authorization-code flow with state, nonce, and PKCE (S256)."""
    _ = request  # required by slowapi
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


OIDC_ALLOWED_ALGS = frozenset({"RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512"})


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

    # Never let the token pick its own algorithm: an attacker-chosen HS256
    # would turn the IdP's *public* key into the HMAC secret.
    alg = header.get("alg")
    if alg not in OIDC_ALLOWED_ALGS:
        raise ValueError(f"id_token alg {alg!r} not allowed")
    key_obj = jwt.PyJWK(key, algorithm=alg).key
    claims = jwt.decode(
        id_token,
        key_obj,
        algorithms=[alg],
        audience=settings.oidc_client_id,
    )
    # Exact issuer match (trailing slash tolerated) -- a prefix match would
    # accept a sibling realm such as ".../realms/rf-evil".
    if str(claims.get("iss") or "").rstrip("/") != issuer:
        raise ValueError("id_token issuer mismatch")
    if claims.get("nonce") != expected_nonce:
        raise ValueError("nonce mismatch")
    return claims


def resolve_oidc_user(db: Session, claims: dict, info: dict) -> User:
    """Find or create the local user for a verified IdP identity.

    Identity is (iss, sub) from the signed id_token. A pre-existing local
    account is linked only by an IdP-*verified* email -- never by
    preferred_username, which any IdP user can choose (an IdP user named
    "admin" must not become the local admin).
    """
    issuer = str(claims.get("iss") or settings.oidc_issuer).rstrip("/")
    subject = claims.get("sub")
    if not subject:
        raise HTTPException(status_code=401, detail="OIDC id_token missing sub")
    if info.get("sub") and info["sub"] != subject:
        raise HTTPException(status_code=401, detail="OIDC userinfo subject mismatch")

    user = (
        db.query(User)
        .filter(User.oidc_issuer == issuer, User.oidc_subject == subject)
        .first()
    )
    if user:
        return user

    email = info.get("email") or claims.get("email")
    email_verified = info.get("email_verified", claims.get("email_verified"))
    if not email or email_verified is not True:
        raise HTTPException(status_code=403, detail="OIDC account has no verified email")

    user = db.query(User).filter(User.email == email).first()
    if user:
        if user.oidc_subject and (user.oidc_issuer, user.oidc_subject) != (issuer, subject):
            raise HTTPException(status_code=403, detail="Account is linked to another SSO identity")
        user.oidc_issuer, user.oidc_subject = issuer, subject
        db.commit()
        db.refresh(user)
        return user

    base = (info.get("preferred_username") or email.split("@")[0])[:90]
    username = base
    while db.query(User).filter(User.username == username).first():
        username = f"{base}-{secrets.token_hex(3)}"
    role = db.query(Role).filter(Role.name == "analyst").first()
    user = User(
        email=email,
        username=username,
        # Unusable local password: SSO users sign in through the IdP only.
        hashed_password=pwd_context.hash(secrets.token_urlsafe(32)),
        full_name=info.get("name") or username,
        role_id=role.id if role else None,
        is_active=True,
        oidc_issuer=issuer,
        oidc_subject=subject,
    )
    db.add(user)
    db.flush()
    ensure_active_membership(db, user)
    db.commit()
    db.refresh(user)
    return user


@router.get("/oidc/callback")
@limiter.limit("20/minute")
async def oidc_callback(
    request: Request,
    code: str,
    state: str | None = None,
    db: Session = Depends(get_db),
):
    """Exchange OIDC code for tokens; redirect with a one-time app code (not JWT)."""
    _ = request  # required by slowapi
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

        # Validate id_token signature via IdP JWKS + nonce. The id_token is the
        # only signed statement of who the user is, so a missing one is fatal.
        id_token = tokens.get("id_token")
        if not id_token:
            raise HTTPException(status_code=401, detail="OIDC response missing id_token")
        try:
            claims = await _verify_oidc_id_token(client, id_token, pending.nonce)
            if claims.get("nonce") != pending.nonce:
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

    user = resolve_oidc_user(db, claims, info)

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
@limiter.limit("20/minute")
async def oidc_exchange(
    request: Request,
    body: SSOExchangeRequest,
    db: Session = Depends(get_db),
):
    """Exchange a one-time SSO code (from callback redirect) for an app JWT."""
    _ = request  # required by slowapi
    from app.services.oidc_store import redeem_one_time_code

    user_id = redeem_one_time_code(body.code)
    if not user_id:
        raise HTTPException(status_code=400, detail="Invalid or expired SSO code")
    user = db.query(User).filter(User.id == user_id, User.is_active == True).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found or inactive")
    return _token_pair(db, user)


@router.get("/sso/status")
async def sso_status():
    return {
        "oidc_enabled": settings.oidc_enabled,
        "issuer": settings.oidc_issuer if settings.oidc_enabled else None,
        "login_url": "/api/auth/oidc/login" if settings.oidc_enabled else None,
    }
