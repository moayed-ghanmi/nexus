import base64
import hashlib
import hmac
import json
import time
from typing import Optional
import bcrypt
from fastapi import Request, Depends, HTTPException, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from app.config import SECRET_KEY, SESSION_COOKIE_NAME, ADMIN_SECRET_URL
from app.database import get_db
from app.models import User, UserRole

def hash_password(password: str) -> str:
    """Hash password using bcrypt."""
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify password against bcrypt hash."""
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))
    except Exception:
        return False

def create_session_token(user_id: int, role: str, expires_in_seconds: int = 86400 * 7) -> str:
    """Create a tamper-proof signed session token containing user_id, role, timestamp."""
    payload = {
        "uid": user_id,
        "role": role,
        "exp": int(time.time()) + expires_in_seconds
    }
    raw_json = json.dumps(payload).encode("utf-8")
    b64_payload = base64.urlsafe_b64encode(raw_json).decode("utf-8")
    
    # Generate HMAC signature
    signature = hmac.new(SECRET_KEY.encode("utf-8"), b64_payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{b64_payload}.{signature}"

def create_temp_2fa_token(user_id: int, role: str) -> str:
    """Create short-lived (5 min) signed token during 2FA challenge."""
    payload = {
        "uid": user_id,
        "role": role,
        "stage": "2fa_pending",
        "exp": int(time.time()) + 300
    }
    raw_json = json.dumps(payload).encode("utf-8")
    b64_payload = base64.urlsafe_b64encode(raw_json).decode("utf-8")
    signature = hmac.new(SECRET_KEY.encode("utf-8"), b64_payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{b64_payload}.{signature}"

def verify_temp_2fa_token(token: str) -> Optional[dict]:
    """Verify short-lived 2FA challenge token."""
    data = verify_session_token(token)
    if data and data.get("stage") == "2fa_pending":
        return data
    return None

def create_password_reset_token(user_id: int, expires_in_seconds: int = 1800) -> str:
    """Create short-lived (30 min) signed token for password reset."""
    payload = {
        "uid": user_id,
        "action": "password_reset",
        "exp": int(time.time()) + expires_in_seconds
    }
    raw_json = json.dumps(payload).encode("utf-8")
    b64_payload = base64.urlsafe_b64encode(raw_json).decode("utf-8")
    signature = hmac.new(SECRET_KEY.encode("utf-8"), b64_payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{b64_payload}.{signature}"

def verify_password_reset_token(token: str) -> Optional[int]:
    """Verify signed password reset token and return user_id if valid."""
    data = verify_session_token(token)
    if data and data.get("action") == "password_reset":
        return data.get("uid")
    return None

def verify_session_token(token: str) -> Optional[dict]:
    """Verify the signature and expiration of the session token."""
    try:
        if not token or "." not in token:
            return None
        b64_payload, signature = token.split(".", 1)
        expected_sig = hmac.new(SECRET_KEY.encode("utf-8"), b64_payload.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected_sig):
            return None
        
        raw_json = base64.urlsafe_b64decode(b64_payload.encode("utf-8"))
        data = json.loads(raw_json.decode("utf-8"))
        
        if data.get("exp", 0) < int(time.time()):
            return None
        return data
    except Exception:
        return None

def get_current_user_from_request(request: Request, db: Session) -> Optional[User]:
    """Retrieve the current user from session cookie if present."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    session_data = verify_session_token(token)
    if not session_data:
        return None
    
    user = db.query(User).filter(User.id == session_data["uid"], User.is_active == True).first()
    return user

def require_login(request: Request, db: Session = Depends(get_db)) -> User:
    """Dependency for routes requiring customer login."""
    user = get_current_user_from_request(request, db)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
            headers={"Location": f"/auth/login?next={request.url.path}"}
        )
    return user

def require_admin(request: Request, db: Session = Depends(get_db)) -> User:
    """
    Strict Admin Guard:
    Only users with role 'admin' or 'super_admin' can pass.
    If unauthorized or anonymous, redirects to the SECRET admin gate URL.
    """
    user = get_current_user_from_request(request, db)
    if not user or not user.is_admin_user:
        raise HTTPException(
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
            headers={"Location": f"{ADMIN_SECRET_URL}/login"}
        )
    return user

def require_super_admin(request: Request, db: Session = Depends(get_db)) -> User:
    """
    Super Admin Guard:
    Restricts access strictly to 'super_admin' role accounts.
    Standard 'admin' staff are intercepted and redirected with clear authorization notices.
    """
    user = require_admin(request, db)
    if user.role != UserRole.SUPER_ADMIN.value:
        accept_header = request.headers.get("accept", "").lower()
        if request.method != "GET" or "application/json" in accept_header:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="ACCESS DENIED: Super Administrator authority required."
            )
        raise HTTPException(
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
            headers={"Location": f"{ADMIN_SECRET_URL}/dashboard?error=super_admin_required"}
        )
    return user
