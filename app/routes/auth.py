from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from pathlib import Path
import urllib.parse
from app.config import SESSION_COOKIE_NAME
from app.database import get_db
from app.models import User, UserRole, DigitalKey
from app.security import (
    hash_password, verify_password, create_session_token, 
    get_current_user_from_request, require_login,
    create_temp_2fa_token, verify_temp_2fa_token,
    create_password_reset_token, verify_password_reset_token
)
from app.services import totp_service
from app.services.email_service import send_password_reset_email

router = APIRouter(prefix="/auth")
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))

@router.get("/login", response_class=HTMLResponse)
async def login_page(
    request: Request, 
    next: str = "/", 
    success: str = None, 
    error: str = None, 
    db: Session = Depends(get_db)
):
    current_user = get_current_user_from_request(request, db)
    if current_user:
        return RedirectResponse(url="/profile", status_code=303)
    
    alert_message = None
    alert_type = None
    if success == "password_reset":
        alert_message = "Your password has been successfully reset! You can now sign in with your new password."
        alert_type = "success"
    elif error == "session_expired":
        alert_message = "Your security session expired. Please sign in again."
        alert_type = "error"

    return templates.TemplateResponse(
        request=request,
        name="auth/login.html",
        context={
            "next_url": next, 
            "current_user": None,
            "alert_message": alert_message,
            "alert_type": alert_type
        }
    )

@router.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.email == email.strip().lower()).first()
    if not user or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            request=request,
            name="auth/login.html",
            context={
                "next_url": next, 
                "current_user": None,
                "alert_message": "Invalid email address or password.",
                "alert_type": "error"
            },
            status_code=400
        )
    
    if not user.is_active:
        return templates.TemplateResponse(
            request=request,
            name="auth/login.html",
            context={
                "next_url": next, 
                "current_user": None,
                "alert_message": "This account is currently suspended. Please contact support.",
                "alert_type": "error"
            },
            status_code=403
        )

    # Phase 15: Check if Two-Factor Authentication (TOTP) is enabled
    if user.is_totp_enabled and user.totp_secret:
        temp_token = create_temp_2fa_token(user.id, user.role)
        target_next = next if next.startswith("/") else "/"
        redirect_url = f"/auth/2fa-verify?next={urllib.parse.quote(target_next)}"
        resp = RedirectResponse(url=redirect_url, status_code=303)
        resp.set_cookie(
            key="nexus_2fa_temp",
            value=temp_token,
            httponly=True,
            max_age=300,
            samesite="lax"
        )
        return resp

    # Valid Customer or Admin login
    token = create_session_token(user.id, user.role)
    response = RedirectResponse(url=next if next.startswith("/") else "/", status_code=303)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        max_age=86400 * 7,
        samesite="lax"
    )
    return response

@router.get("/2fa-verify", response_class=HTMLResponse)
async def totp_verify_page(request: Request, next: str = "/", db: Session = Depends(get_db)):
    """Renders 6-digit TOTP verification challenge page."""
    temp_token = request.cookies.get("nexus_2fa_temp")
    temp_data = verify_temp_2fa_token(temp_token) if temp_token else None
    if not temp_data:
        return RedirectResponse(url="/auth/login", status_code=303)

    return templates.TemplateResponse(
        request=request,
        name="auth/2fa_verify.html",
        context={
            "next_url": next,
            "verify_url": "/auth/2fa-verify",
            "current_user": None,
            "alert_message": None
        }
    )

@router.post("/2fa-verify", response_class=HTMLResponse)
async def totp_verify_submit(
    request: Request,
    code: str = Form(...),
    next: str = Form("/"),
    db: Session = Depends(get_db)
):
    """Validates 6-digit TOTP code and grants session token."""
    temp_token = request.cookies.get("nexus_2fa_temp")
    temp_data = verify_temp_2fa_token(temp_token) if temp_token else None
    if not temp_data:
        return RedirectResponse(url="/auth/login?error=session_expired", status_code=303)

    user = db.query(User).filter(User.id == temp_data["uid"], User.is_active == True).first()
    if not user or not user.totp_secret:
        return RedirectResponse(url="/auth/login", status_code=303)

    # Validate TOTP code
    is_valid = totp_service.verify_totp(user.totp_secret, code)
    if not is_valid:
        return templates.TemplateResponse(
            request=request,
            name="auth/2fa_verify.html",
            context={
                "next_url": next,
                "verify_url": "/auth/2fa-verify",
                "current_user": None,
                "alert_message": "Invalid 6-digit code. Please verify the code in your Authenticator app and try again."
            },
            status_code=400
        )

    # Success: issue full session cookie
    token = create_session_token(user.id, user.role)
    target = next if next.startswith("/") else "/"
    response = RedirectResponse(url=target, status_code=303)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        max_age=86400 * 7,
        samesite="lax"
    )
    response.delete_cookie("nexus_2fa_temp")
    return response

@router.post("/2fa/generate-secret")
async def generate_2fa_secret(request: Request, db: Session = Depends(get_db)):
    """Generates a new Base32 TOTP secret and provisioning URI for Google Authenticator setup."""
    current_user = get_current_user_from_request(request, db)
    if not current_user:
        return JSONResponse({"success": False, "error": "auth_required"}, status_code=401)

    secret = totp_service.generate_secret()
    uri = totp_service.get_provisioning_uri(secret, current_user.email, issuer="NEXUS KEYS")

    return JSONResponse({
        "success": True,
        "secret": secret,
        "uri": uri,
        "email": current_user.email
    })

@router.post("/2fa/enable")
async def enable_2fa(request: Request, db: Session = Depends(get_db)):
    """Verifies confirmation code against secret and enables 2FA on customer account."""
    current_user = get_current_user_from_request(request, db)
    if not current_user:
        return JSONResponse({"success": False, "error": "auth_required"}, status_code=401)

    payload = await request.json()
    secret = payload.get("secret", "").strip().upper()
    code = payload.get("code", "").strip()

    if not secret or not code:
        return JSONResponse({"success": False, "error": "Secret and verification code are required."}, status_code=400)

    if not totp_service.verify_totp(secret, code):
        return JSONResponse({"success": False, "error": "Invalid verification code. Please check your authenticator app."}, status_code=400)

    current_user.totp_secret = secret
    current_user.is_totp_enabled = True
    db.commit()

    return JSONResponse({
        "success": True,
        "message": "Two-Factor Authentication (2FA) is now enabled on your account!"
    })

@router.post("/2fa/disable")
async def disable_2fa(request: Request, db: Session = Depends(get_db)):
    """Disables 2FA on customer account."""
    current_user = get_current_user_from_request(request, db)
    if not current_user:
        return JSONResponse({"success": False, "error": "auth_required"}, status_code=401)

    current_user.is_totp_enabled = False
    current_user.totp_secret = None
    db.commit()

    return JSONResponse({
        "success": True,
        "message": "Two-Factor Authentication has been disabled."
    })

@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request, ref: str = None, db: Session = Depends(get_db)):
    current_user = get_current_user_from_request(request, db)
    if current_user:
        return RedirectResponse(url="/profile", status_code=303)
    
    return templates.TemplateResponse(
        request=request,
        name="auth/register.html",
        context={"current_user": None, "ref_code": (ref or "").strip().upper()}
    )

@router.post("/register", response_class=HTMLResponse)
async def register_submit(
    request: Request,
    full_name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    referral_code: str = Form(None),
    db: Session = Depends(get_db)
):
    clean_email = email.strip().lower()
    existing = db.query(User).filter(User.email == clean_email).first()
    if existing:
        return templates.TemplateResponse(
            request=request,
            name="auth/register.html",
            context={
                "current_user": None,
                "alert_message": "An account with this email address already exists.",
                "alert_type": "error",
                "ref_code": referral_code or ""
            },
            status_code=400
        )

    # Process optional referral code
    referred_by_id = None
    if referral_code and referral_code.strip():
        referrer = db.query(User).filter(User.referral_code == referral_code.strip().upper()).first()
        if referrer:
            referred_by_id = referrer.id

    import uuid
    new_user = User(
        full_name=full_name.strip(),
        email=clean_email,
        password_hash=hash_password(password),
        role=UserRole.CUSTOMER.value,
        is_active=True,
        referred_by_id=referred_by_id,
        referral_code=f"REF-{uuid.uuid4().hex[:6].upper()}"
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    token = create_session_token(new_user.id, new_user.role)
    response = RedirectResponse(url="/profile", status_code=303)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        max_age=86400 * 7,
        samesite="lax"
    )
    return response

@router.get("/logout")
async def logout(request: Request):
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response

@router.get("/forgot-password", response_class=HTMLResponse)
async def forgot_password_page(request: Request, db: Session = Depends(get_db)):
    """Renders the forgot password email submission page."""
    current_user = get_current_user_from_request(request, db)
    if current_user:
        return RedirectResponse(url="/profile", status_code=303)
    
    return templates.TemplateResponse(
        request=request,
        name="auth/forgot_password.html",
        context={
            "current_user": None,
            "alert_message": None,
            "alert_type": None,
            "reset_link": None
        }
    )

@router.post("/forgot-password", response_class=HTMLResponse)
async def forgot_password_submit(
    request: Request,
    email: str = Form(...),
    db: Session = Depends(get_db)
):
    """Processes password reset request, generates token, and dispatches reset email."""
    clean_email = email.strip().lower()
    user = db.query(User).filter(User.email == clean_email, User.is_active == True).first()
    
    reset_url = None
    if user:
        token = create_password_reset_token(user.id, expires_in_seconds=1800)
        reset_url = str(request.base_url).rstrip("/") + f"/auth/reset-password?token={token}"
        send_password_reset_email(user, reset_url, db)
    
    return templates.TemplateResponse(
        request=request,
        name="auth/forgot_password.html",
        context={
            "current_user": None,
            "alert_message": f"If an active account exists for {clean_email}, a password reset link has been dispatched to your inbox.",
            "alert_type": "success",
            "reset_link": reset_url
        }
    )

@router.get("/reset-password", response_class=HTMLResponse)
async def reset_password_page(
    request: Request,
    token: str = "",
    db: Session = Depends(get_db)
):
    """Validates reset token and displays new password input form."""
    user_id = verify_password_reset_token(token) if token else None
    if not user_id:
        return templates.TemplateResponse(
            request=request,
            name="auth/forgot_password.html",
            context={
                "current_user": None,
                "alert_message": "This password reset link is invalid or has expired. Please request a new one.",
                "alert_type": "error",
                "reset_link": None
            },
            status_code=400
        )
    
    user = db.query(User).filter(User.id == user_id, User.is_active == True).first()
    if not user:
        return templates.TemplateResponse(
            request=request,
            name="auth/forgot_password.html",
            context={
                "current_user": None,
                "alert_message": "User account associated with this reset link was not found.",
                "alert_type": "error",
                "reset_link": None
            },
            status_code=400
        )
    
    return templates.TemplateResponse(
        request=request,
        name="auth/reset_password.html",
        context={
            "current_user": None,
            "token": token,
            "user_email": user.email,
            "alert_message": None,
            "alert_type": None
        }
    )

@router.post("/reset-password", response_class=HTMLResponse)
async def reset_password_submit(
    request: Request,
    token: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
    db: Session = Depends(get_db)
):
    """Validates token, verifies new password match, and updates user password hash."""
    user_id = verify_password_reset_token(token) if token else None
    if not user_id:
        return templates.TemplateResponse(
            request=request,
            name="auth/forgot_password.html",
            context={
                "current_user": None,
                "alert_message": "Your password reset session has expired. Please submit a new request.",
                "alert_type": "error",
                "reset_link": None
            },
            status_code=400
        )

    user = db.query(User).filter(User.id == user_id, User.is_active == True).first()
    if not user:
        return templates.TemplateResponse(
            request=request,
            name="auth/forgot_password.html",
            context={
                "current_user": None,
                "alert_message": "User account could not be found.",
                "alert_type": "error",
                "reset_link": None
            },
            status_code=400
        )

    if password != confirm_password:
        return templates.TemplateResponse(
            request=request,
            name="auth/reset_password.html",
            context={
                "current_user": None,
                "token": token,
                "user_email": user.email,
                "alert_message": "Passwords do not match. Please verify and try again.",
                "alert_type": "error"
            },
            status_code=400
        )

    if len(password) < 6:
        return templates.TemplateResponse(
            request=request,
            name="auth/reset_password.html",
            context={
                "current_user": None,
                "token": token,
                "user_email": user.email,
                "alert_message": "Password must be at least 6 characters in length.",
                "alert_type": "error"
            },
            status_code=400
        )

    # Successfully update password
    user.password_hash = hash_password(password)
    db.commit()

    return RedirectResponse(url="/auth/login?success=password_reset", status_code=303)

