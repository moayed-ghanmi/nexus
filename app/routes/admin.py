import json
import re
import random
import csv
import io
from datetime import datetime, timezone
from fastapi import APIRouter, Request, Depends, Form, HTTPException, Response
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func
from pathlib import Path
from app.config import ADMIN_SECRET_URL, SESSION_COOKIE_NAME
from app.database import get_db
from app.models import (
    User, UserRole, Category, Product, DigitalKey, KeyStatus, 
    PaymentGateway, Order, OrderItem, OrderStatus, AdminAuditLog,
    Coupon, ProductReview, SiteSetting,
    Ticket, TicketMessage, TicketStatus, TicketPriority,
    WalletTransaction, GiftVoucher
)
from app.security import (
    hash_password, verify_password, create_session_token, 
    get_current_user_from_request, require_admin, require_super_admin,
    create_temp_2fa_token, verify_temp_2fa_token
)
from app.rate_limiter import check_ip_rate_limit, record_failed_attempt, reset_failed_attempts
from app.services.email_service import send_order_receipt_email, send_test_email
from app.services.invoice_service import generate_order_invoice_pdf
from app.services import wallet_service, loyalty_service, totp_service

router = APIRouter(prefix=ADMIN_SECRET_URL)
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))

def log_admin_action(db: Session, admin_id: int, action: str, target: str, details: str = None, ip: str = None):
    """Helper to record administrative actions into audit ledger."""
    audit = AdminAuditLog(
        admin_id=admin_id,
        action=action,
        target=target,
        details=details,
        ip_address=ip or "127.0.0.1",
        created_at=datetime.now(timezone.utc)
    )
    db.add(audit)
    db.commit()

# ==========================================
# 1. AUTH & DASHBOARD
# ==========================================

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def admin_root(request: Request, db: Session = Depends(get_db)):
    """Secret portal entry point."""
    user = get_current_user_from_request(request, db)
    if user and user.is_admin_user:
        return RedirectResponse(url=f"{ADMIN_SECRET_URL}/dashboard", status_code=303)
    return RedirectResponse(url=f"{ADMIN_SECRET_URL}/login", status_code=303)

@router.get("/login", response_class=HTMLResponse)
async def admin_login_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_from_request(request, db)
    if user and user.is_admin_user:
        return RedirectResponse(url=f"{ADMIN_SECRET_URL}/dashboard", status_code=303)
    
    return templates.TemplateResponse(
        request=request,
        name="admin/login.html",
        context={
            "admin_secret_url": ADMIN_SECRET_URL,
            "error_message": None
        }
    )

@router.post("/login", response_class=HTMLResponse)
async def admin_login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    client_ip = request.client.host if request.client else "127.0.0.1"
    is_allowed, wait_sec = check_ip_rate_limit(client_ip)
    if not is_allowed:
        return templates.TemplateResponse(
            request=request,
            name="admin/login.html",
            context={
                "admin_secret_url": ADMIN_SECRET_URL,
                "error_message": f"SECURITY LOCKOUT: Too many failed login attempts. Retry in {wait_sec} seconds."
            },
            status_code=429
        )

    clean_email = email.strip().lower()
    user = db.query(User).filter(User.email == clean_email).first()

    if not user or not verify_password(password, user.password_hash):
        record_failed_attempt(client_ip)
        return templates.TemplateResponse(
            request=request,
            name="admin/login.html",
            context={
                "admin_secret_url": ADMIN_SECRET_URL,
                "error_message": "Invalid credentials or unauthorized terminal access."
            },
            status_code=401
        )
    
    if not user.is_admin_user:
        record_failed_attempt(client_ip)
        return templates.TemplateResponse(
            request=request,
            name="admin/login.html",
            context={
                "admin_secret_url": ADMIN_SECRET_URL,
                "error_message": "ACCESS DENIED: Account does not possess Administrative privileges."
            },
            status_code=403
        )

    # Phase 15: Two-Factor Authentication (2FA) verification gate
    if user.is_totp_enabled and user.totp_secret:
        reset_failed_attempts(client_ip)
        temp_token = create_temp_2fa_token(user.id, user.role)
        resp = RedirectResponse(url=f"{ADMIN_SECRET_URL}/2fa-verify", status_code=303)
        resp.set_cookie(
            key="nexus_admin_2fa_temp",
            value=temp_token,
            httponly=True,
            max_age=300,
            samesite="lax"
        )
        return resp

    # Valid Administrator -> Clear failed attempts
    reset_failed_attempts(client_ip)
    token = create_session_token(user.id, user.role)
    response = RedirectResponse(url=f"{ADMIN_SECRET_URL}/dashboard", status_code=303)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        max_age=86400 * 7,
        samesite="lax"
    )
    return response

@router.get("/2fa-verify", response_class=HTMLResponse)
async def admin_2fa_verify_page(request: Request, db: Session = Depends(get_db)):
    """Renders 6-digit administrative TOTP verification challenge page."""
    temp_token = request.cookies.get("nexus_admin_2fa_temp")
    temp_data = verify_temp_2fa_token(temp_token) if temp_token else None
    if not temp_data:
        return RedirectResponse(url=f"{ADMIN_SECRET_URL}/login", status_code=303)

    return templates.TemplateResponse(
        request=request,
        name="auth/2fa_verify.html",
        context={
            "next_url": f"{ADMIN_SECRET_URL}/dashboard",
            "verify_url": f"{ADMIN_SECRET_URL}/2fa-verify",
            "current_user": None,
            "alert_message": None
        }
    )

@router.post("/2fa-verify", response_class=HTMLResponse)
async def admin_2fa_verify_submit(
    request: Request,
    code: str = Form(...),
    db: Session = Depends(get_db)
):
    """Validates 6-digit TOTP code and grants administrative session."""
    temp_token = request.cookies.get("nexus_admin_2fa_temp")
    temp_data = verify_temp_2fa_token(temp_token) if temp_token else None
    if not temp_data:
        return RedirectResponse(url=f"{ADMIN_SECRET_URL}/login?error=expired", status_code=303)

    user = db.query(User).filter(User.id == temp_data["uid"], User.is_active == True).first()
    if not user or not user.totp_secret:
        return RedirectResponse(url=f"{ADMIN_SECRET_URL}/login", status_code=303)

    if not totp_service.verify_totp(user.totp_secret, code):
        return templates.TemplateResponse(
            request=request,
            name="auth/2fa_verify.html",
            context={
                "next_url": f"{ADMIN_SECRET_URL}/dashboard",
                "verify_url": f"{ADMIN_SECRET_URL}/2fa-verify",
                "current_user": None,
                "alert_message": "Invalid 6-digit administrative authenticator code. Access Denied."
            },
            status_code=400
        )

    # Valid TOTP: issue admin session token
    token = create_session_token(user.id, user.role)
    response = RedirectResponse(url=f"{ADMIN_SECRET_URL}/dashboard", status_code=303)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        max_age=86400 * 7,
        samesite="lax"
    )
    response.delete_cookie("nexus_admin_2fa_temp")
    log_admin_action(db, user.id, "ADMIN_2FA_LOGIN", user.email, "Two-Factor Authentication challenge passed successfully")
    return response

@router.post("/admins/{admin_id}/reset-2fa")
async def admin_reset_admin_2fa(
    admin_id: int,
    current_admin: User = Depends(require_super_admin),
    db: Session = Depends(get_db)
):
    """Super Admin can reset/disable 2FA for an admin account if their device is lost."""
    target = db.query(User).filter(User.id == admin_id).first()
    if not target:
        return JSONResponse({"success": False, "error": "Admin account not found"}, status_code=404)

    target.is_totp_enabled = False
    target.totp_secret = None
    db.commit()

    log_admin_action(db, current_admin.id, "ADMIN_2FA_RESET", target.email, "Super Administrator reset Two-Factor Authentication")
    return RedirectResponse(url=f"{ADMIN_SECRET_URL}/admins?success=2fa_reset", status_code=303)

@router.post("/reset-rate-limit")
async def reset_rate_limit_endpoint(request: Request):
    client_ip = request.client.host if request.client else "127.0.0.1"
    reset_failed_attempts(client_ip)
    return JSONResponse({"success": True, "message": f"Rate limiter cleared for {client_ip}"})

@router.get("/dashboard", response_class=HTMLResponse)
async def admin_dashboard(
    request: Request,
    status: str = "all",
    current_admin: User = Depends(require_admin), 
    db: Session = Depends(get_db)
):
    all_orders = db.query(Order).order_by(Order.id.desc()).all()

    # Financial metrics calculated strictly from completed orders
    completed_orders = [o for o in all_orders if o.status == OrderStatus.COMPLETED.value]
    total_revenue = round(sum(o.total_amount for o in completed_orders), 2)
    total_cost = round(sum(o.total_cost for o in completed_orders), 2)
    total_profit = round(sum(o.profit for o in completed_orders), 2)
    profit_margin = round((total_profit / total_revenue * 100), 1) if total_revenue > 0 else 0.0
    aov = round(total_revenue / len(completed_orders), 2) if completed_orders else 0.0

    # Order status breakdown metrics
    count_all = len(all_orders)
    count_completed = len(completed_orders)
    count_pending = sum(1 for o in all_orders if o.status == OrderStatus.PENDING.value)
    count_refunded = sum(1 for o in all_orders if o.status == OrderStatus.REFUNDED.value)
    count_cancelled = sum(1 for o in all_orders if o.status == OrderStatus.CANCELLED.value)
    total_refunded_amount = round(sum(o.total_amount for o in all_orders if o.status == OrderStatus.REFUNDED.value), 2)

    total_products = db.query(Product).count()
    available_keys = db.query(DigitalKey).filter(DigitalKey.status == KeyStatus.AVAILABLE.value).count()
    sold_keys = db.query(DigitalKey).filter(DigitalKey.status == KeyStatus.SOLD.value).count()
    total_clients = db.query(User).filter(User.role == UserRole.CUSTOMER.value).count()
    active_gateways = db.query(PaymentGateway).filter(PaymentGateway.is_enabled == True).count()
    
    products = db.query(Product).all()
    low_stock_products = [p for p in products if p.available_keys_count <= 2]

    # Filter orders according to selected tab
    clean_status = status.strip().lower()
    if clean_status in [OrderStatus.COMPLETED.value, OrderStatus.PENDING.value, OrderStatus.REFUNDED.value, OrderStatus.CANCELLED.value]:
        filtered_orders = [o for o in all_orders if o.status == clean_status]
    else:
        clean_status = "all"
        filtered_orders = all_orders

    stats = {
        "total_revenue": total_revenue,
        "total_cost": total_cost,
        "total_profit": total_profit,
        "profit_margin": profit_margin,
        "aov": aov,
        "total_orders": count_completed,
        "count_all": count_all,
        "count_completed": count_completed,
        "count_pending": count_pending,
        "count_refunded": count_refunded,
        "count_cancelled": count_cancelled,
        "total_refunded_amount": total_refunded_amount,
        "total_products": total_products,
        "available_keys": available_keys,
        "sold_keys": sold_keys,
        "total_clients": total_clients,
        "active_gateways": active_gateways
    }

    return templates.TemplateResponse(
        request=request,
        name="admin/dashboard.html",
        context={
            "current_admin": current_admin,
            "admin_secret_url": ADMIN_SECRET_URL,
            "stats": stats,
            "status_filter": clean_status,
            "products": products,
            "low_stock_products": low_stock_products,
            "recent_orders": filtered_orders
        }
    )

# ==========================================
# 2. PRODUCTS & DIGITAL KEYS CRUD CONTROLLER
# ==========================================

@router.get("/products", response_class=HTMLResponse)
async def admin_products_page(
    request: Request, 
    current_admin: User = Depends(require_admin), 
    db: Session = Depends(get_db)
):
    products = db.query(Product).order_by(Product.id.desc()).all()
    categories = db.query(Category).all()
    return templates.TemplateResponse(
        request=request,
        name="admin/products.html",
        context={
            "current_admin": current_admin,
            "admin_secret_url": ADMIN_SECRET_URL,
            "products": products,
            "categories": categories
        }
    )

@router.post("/products/add")
async def admin_add_product(
    title: str = Form(...),
    category_id: int = Form(...),
    platform: str = Form("Global"),
    region: str = Form("GLOBAL"),
    price: float = Form(...),
    cost_price: float = Form(0.0),
    badge: str = Form(""),
    image_url: str = Form(""),
    description: str = Form(""),
    initial_keys: str = Form(""),
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    clean_slug = re.sub(r'[^a-zA-Z0-9]+', '-', title.strip().lower()).strip('-')
    if db.query(Product).filter(Product.slug == clean_slug).first():
        clean_slug = f"{clean_slug}-{random.randint(100, 999)}"

    new_prod = Product(
        title=title.strip(),
        slug=clean_slug,
        category_id=category_id,
        platform=platform.strip(),
        region=region.strip().upper(),
        price=price,
        cost_price=cost_price,
        badge=badge.strip().upper() if badge else None,
        image_url=image_url.strip() or "https://images.unsplash.com/photo-1612287232230-e3778dfc5c70?auto=format&fit=crop&w=600&q=80",
        description=description.strip(),
        is_active=True
    )
    db.add(new_prod)
    db.commit()
    db.refresh(new_prod)

    # Bulk add initial keys if provided
    if initial_keys:
        raw_keys = [k.strip() for k in initial_keys.splitlines() if k.strip()]
        for code in raw_keys:
            db.add(DigitalKey(product_id=new_prod.id, key_code=code, status=KeyStatus.AVAILABLE.value))
        db.commit()

    log_admin_action(db, current_admin.id, "PRODUCT_CREATE", f"Product ID {new_prod.id}: {new_prod.title}")
    return RedirectResponse(url=f"{ADMIN_SECRET_URL}/products", status_code=303)

@router.post("/products/{product_id}/keys/add")
async def admin_bulk_add_keys(
    product_id: int,
    keys_text: str = Form(...),
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    raw_keys = [k.strip() for k in keys_text.splitlines() if k.strip()]
    count = 0
    for code in raw_keys:
        db.add(DigitalKey(product_id=product.id, key_code=code, status=KeyStatus.AVAILABLE.value))
        count += 1
    db.commit()

    # Phase 15: Automated Wishlist Restock Radar Dispatcher
    try:
        from app.services.email_service import dispatch_product_restock_notifications
        dispatched_alerts = dispatch_product_restock_notifications(product.id, count, db)
        if dispatched_alerts > 0:
            log_admin_action(db, current_admin.id, "RESTOCK_ALERTS_SENT", f"Dispatched {dispatched_alerts} restock alerts for {product.title}")
    except Exception as ex:
        print(f"[Admin] Restock alert dispatch error: {ex}")

    log_admin_action(db, current_admin.id, "KEYS_RESTOCK", f"Restocked {count} keys for {product.title}")
    return RedirectResponse(url=f"{ADMIN_SECRET_URL}/products", status_code=303)

@router.get("/products/{product_id}/details")
async def admin_get_product_details(
    product_id: int,
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Returns JSON product information for pre-populating admin edit modals."""
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        return JSONResponse({"success": False, "error": "Product not found."}, status_code=404)

    return JSONResponse({
        "success": True,
        "product": {
            "id": product.id,
            "title": product.title,
            "slug": product.slug,
            "category_id": product.category_id,
            "category_name": product.category.name if product.category else "Uncategorized",
            "platform": product.platform or "Global",
            "region": product.region or "GLOBAL",
            "price": product.price,
            "cost_price": product.cost_price,
            "profit": product.estimated_profit_per_unit,
            "badge": product.badge or "",
            "image_url": product.image_url or "",
            "description": product.description or "",
            "is_active": product.is_active,
            "available_keys_count": product.available_keys_count
        }
    })

@router.post("/products/{product_id}/update")
async def admin_update_product(
    product_id: int,
    title: str = Form(...),
    category_id: int = Form(...),
    platform: str = Form("Global"),
    region: str = Form("GLOBAL"),
    price: float = Form(...),
    cost_price: float = Form(0.0),
    badge: str = Form(""),
    image_url: str = Form(""),
    description: str = Form(""),
    is_active: str = Form("true"),
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Updates product catalog information, pricing, categorization and visibility."""
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        return RedirectResponse(url=f"{ADMIN_SECRET_URL}/products?error=not_found", status_code=303)

    old_title = product.title
    clean_title = title.strip()

    product.title = clean_title
    product.category_id = category_id
    product.platform = platform.strip()
    product.region = region.strip().upper()
    product.price = max(0.0, round(float(price), 2))
    product.cost_price = max(0.0, round(float(cost_price), 2))
    product.badge = badge.strip().upper() if badge.strip() else None
    if image_url.strip():
        product.image_url = image_url.strip()
    product.description = description.strip()
    product.is_active = is_active.lower() in ["true", "1", "yes", "on"]

    # Regenerate slug if title changed
    if clean_title.lower() != old_title.lower():
        clean_slug = re.sub(r'[^a-zA-Z0-9]+', '-', clean_title.lower()).strip('-')
        existing_slug = db.query(Product).filter(Product.slug == clean_slug, Product.id != product.id).first()
        if existing_slug:
            clean_slug = f"{clean_slug}-{random.randint(100, 999)}"
        product.slug = clean_slug

    db.commit()

    log_admin_action(
        db=db,
        admin_id=current_admin.id,
        action="PRODUCT_UPDATE",
        target=f"Product #{product.id}: {product.title}",
        details=f"Updated details: Price=${product.price:.2f}, Cost=${product.cost_price:.2f}, Active={product.is_active}, Platform={product.platform}"
    )

    return RedirectResponse(url=f"{ADMIN_SECRET_URL}/products?success=updated", status_code=303)

@router.post("/products/{product_id}/toggle-active")
async def admin_toggle_product_active(
    product_id: int,
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Instantly toggles catalog visibility (Active vs Hidden/Draft)."""
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        return JSONResponse({"success": False, "error": "Product not found."}, status_code=404)

    product.is_active = not product.is_active
    db.commit()

    state = "Active" if product.is_active else "Draft/Hidden"
    log_admin_action(
        db=db,
        admin_id=current_admin.id,
        action="PRODUCT_TOGGLE",
        target=f"Product #{product.id}",
        details=f"Visibility set to {state}"
    )

    return JSONResponse({
        "success": True,
        "is_active": product.is_active,
        "product_id": product.id,
        "message": f"Product '{product.title}' visibility updated to {state}."
    })

@router.post("/products/{product_id}/delete")
async def admin_delete_product(
    product_id: int,
    current_admin: User = Depends(require_super_admin),
    db: Session = Depends(get_db)
):
    product = db.query(Product).filter(Product.id == product_id).first()
    if product:
        title = product.title
        db.delete(product)
        db.commit()
        log_admin_action(db, current_admin.id, "PRODUCT_DELETE", f"Deleted {title}")

    return RedirectResponse(url=f"{ADMIN_SECRET_URL}/products", status_code=303)

# ==========================================
# 2.5. PRODUCT CATEGORIES CONTROLLER
# ==========================================

@router.get("/categories", response_class=HTMLResponse)
async def admin_categories_page(
    request: Request,
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    categories = db.query(Category).order_by(Category.display_order.asc(), Category.id.asc()).all()
    categories_data = []
    for c in categories:
        categories_data.append({
            "id": c.id,
            "name": c.name,
            "slug": c.slug,
            "icon": c.icon or "gamepad-2",
            "description": c.description or "",
            "display_order": c.display_order or 0,
            "products_count": len(c.products)
        })

    return templates.TemplateResponse(
        request=request,
        name="admin/categories.html",
        context={
            "current_admin": current_admin,
            "categories": categories_data,
            "admin_secret_url": ADMIN_SECRET_URL,
            "success": request.query_params.get("success"),
            "error": request.query_params.get("error")
        }
    )

@router.get("/categories/{category_id}/details")
async def admin_category_details(
    category_id: int,
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    cat = db.query(Category).filter(Category.id == category_id).first()
    if not cat:
        return JSONResponse({"success": False, "error": "Category not found."}, status_code=404)
    return JSONResponse({
        "success": True,
        "category": {
            "id": cat.id,
            "name": cat.name,
            "slug": cat.slug,
            "icon": cat.icon or "gamepad-2",
            "description": cat.description or "",
            "display_order": cat.display_order or 0,
            "products_count": len(cat.products)
        }
    })

@router.post("/categories/add")
async def admin_add_category(
    name: str = Form(...),
    slug: str = Form(None),
    icon: str = Form("gamepad-2"),
    description: str = Form(""),
    display_order: int = Form(0),
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    clean_name = name.strip()
    clean_slug = (slug or "").strip().lower()
    if not clean_slug:
        clean_slug = re.sub(r'[^a-z0-9]+', '-', clean_name.lower()).strip('-')

    existing_slug = db.query(Category).filter(Category.slug == clean_slug).first()
    if existing_slug:
        return RedirectResponse(
            url=f"{ADMIN_SECRET_URL}/categories?error=Category+with+this+slug+already+exists", 
            status_code=303
        )

    cat = Category(
        name=clean_name,
        slug=clean_slug,
        icon=icon.strip() if icon else "gamepad-2",
        description=description.strip() if description else None,
        display_order=display_order
    )
    db.add(cat)
    db.commit()

    log_admin_action(db, current_admin.id, "CATEGORY_CREATE", cat.name, f"Created category '{cat.name}' (slug: {cat.slug})")
    return RedirectResponse(url=f"{ADMIN_SECRET_URL}/categories?success=Category+created+successfully", status_code=303)

@router.post("/categories/{category_id}/update")
async def admin_update_category(
    category_id: int,
    name: str = Form(...),
    slug: str = Form(...),
    icon: str = Form("gamepad-2"),
    description: str = Form(""),
    display_order: int = Form(0),
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    cat = db.query(Category).filter(Category.id == category_id).first()
    if not cat:
        return RedirectResponse(url=f"{ADMIN_SECRET_URL}/categories?error=Category+not+found", status_code=303)

    clean_slug = slug.strip().lower()
    existing_slug = db.query(Category).filter(Category.slug == clean_slug, Category.id != category_id).first()
    if existing_slug:
        return RedirectResponse(
            url=f"{ADMIN_SECRET_URL}/categories?error=Slug+already+used+by+another+category", 
            status_code=303
        )

    old_name = cat.name
    cat.name = name.strip()
    cat.slug = clean_slug
    cat.icon = icon.strip() if icon else "gamepad-2"
    cat.description = description.strip() if description else None
    cat.display_order = display_order
    db.commit()

    log_admin_action(db, current_admin.id, "CATEGORY_UPDATE", cat.name, f"Updated category '{old_name}' -> '{cat.name}'")
    return RedirectResponse(url=f"{ADMIN_SECRET_URL}/categories?success=Category+updated+successfully", status_code=303)

@router.post("/categories/{category_id}/delete")
async def admin_delete_category(
    category_id: int,
    current_admin: User = Depends(require_super_admin),
    db: Session = Depends(get_db)
):
    cat = db.query(Category).filter(Category.id == category_id).first()
    if not cat:
        return RedirectResponse(url=f"{ADMIN_SECRET_URL}/categories?error=Category+not+found", status_code=303)

    if len(cat.products) > 0:
        return RedirectResponse(
            url=f"{ADMIN_SECRET_URL}/categories?error=Cannot+delete+category+containing+{len(cat.products)}+products.+Reassign+them+first.",
            status_code=303
        )

    cat_name = cat.name
    db.delete(cat)
    db.commit()

    log_admin_action(db, current_admin.id, "CATEGORY_DELETE", cat_name, f"Deleted category '{cat_name}'")
    return RedirectResponse(url=f"{ADMIN_SECRET_URL}/categories?success=Category+deleted+successfully", status_code=303)

# ==========================================
# 3. PAYMENT GATEWAYS CONTROLLER
# ==========================================

@router.get("/gateways", response_class=HTMLResponse)
async def admin_gateways_page(
    request: Request, 
    current_admin: User = Depends(require_admin), 
    db: Session = Depends(get_db)
):
    gateways = db.query(PaymentGateway).order_by(PaymentGateway.display_order.asc()).all()
    return templates.TemplateResponse(
        request=request,
        name="admin/gateways.html",
        context={
            "current_admin": current_admin,
            "admin_secret_url": ADMIN_SECRET_URL,
            "gateways": gateways
        }
    )

@router.post("/gateways/{gateway_id}/toggle")
async def admin_toggle_gateway(
    gateway_id: int,
    current_admin: User = Depends(require_super_admin),
    db: Session = Depends(get_db)
):
    gateway = db.query(PaymentGateway).filter(PaymentGateway.id == gateway_id).first()
    if gateway:
        gateway.is_enabled = not gateway.is_enabled
        db.commit()
        log_admin_action(db, current_admin.id, "GATEWAY_TOGGLE", f"Toggled {gateway.name} to {gateway.is_enabled}")

    return RedirectResponse(url=f"{ADMIN_SECRET_URL}/gateways", status_code=303)

@router.post("/gateways/{gateway_id}/update")
async def admin_update_gateway(
    gateway_id: int,
    fee_percentage: float = Form(0.0),
    instructions: str = Form(""),
    current_admin: User = Depends(require_super_admin),
    db: Session = Depends(get_db)
):
    gateway = db.query(PaymentGateway).filter(PaymentGateway.id == gateway_id).first()
    if gateway:
        gateway.fee_percentage = fee_percentage
        gateway.instructions = instructions.strip()
        db.commit()
        log_admin_action(db, current_admin.id, "GATEWAY_UPDATE", f"Updated settings for {gateway.name}")

    return RedirectResponse(url=f"{ADMIN_SECRET_URL}/gateways", status_code=303)

# ==========================================
# 4. CLIENTS MANAGEMENT CONTROLLER
# ==========================================

@router.get("/clients", response_class=HTMLResponse)
async def admin_clients_page(
    request: Request, 
    q: str = None,
    current_admin: User = Depends(require_admin), 
    db: Session = Depends(get_db)
):
    query = db.query(User).filter(User.role == UserRole.CUSTOMER.value)
    if q:
        query = query.filter((User.full_name.ilike(f"%{q}%")) | (User.email.ilike(f"%{q}%")))
    
    clients = query.order_by(User.id.desc()).all()

    # Precalculate client spend metrics
    client_metrics = []
    for c in clients:
        spend = sum(o.total_amount for o in c.orders if o.status == OrderStatus.COMPLETED.value)
        keys_count = sum(len(o.assigned_keys) for o in c.orders)
        client_metrics.append({
            "client": c,
            "total_spend": round(spend, 2),
            "orders_count": len(c.orders),
            "keys_count": keys_count
        })

    return templates.TemplateResponse(
        request=request,
        name="admin/clients.html",
        context={
            "current_admin": current_admin,
            "admin_secret_url": ADMIN_SECRET_URL,
            "client_metrics": client_metrics,
            "search_query": q
        }
    )

@router.post("/clients/{client_id}/toggle-status")
async def admin_toggle_client_status(
    client_id: int,
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    client = db.query(User).filter(User.id == client_id, User.role == UserRole.CUSTOMER.value).first()
    if client:
        client.is_active = not client.is_active
        db.commit()
        status_label = "Active" if client.is_active else "Suspended/Banned"
        log_admin_action(db, current_admin.id, "CLIENT_STATUS_CHANGE", f"Changed status of {client.email} to {status_label}")

    return RedirectResponse(url=f"{ADMIN_SECRET_URL}/clients", status_code=303)

@router.post("/clients/{client_id}/wallet-adjust")
async def admin_adjust_client_wallet(
    client_id: int,
    action: str = Form("credit"),
    amount: float = Form(...),
    reason: str = Form("Admin manual adjustment"),
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Allows administrators to manually credit or debit customer store credit with full audit logging."""
    client = db.query(User).filter(User.id == client_id, User.role == UserRole.CUSTOMER.value).first()
    if not client:
        raise HTTPException(status_code=404, detail="Customer not found")

    amount = round(abs(float(amount)), 2)
    if amount <= 0:
        return RedirectResponse(url=f"{ADMIN_SECRET_URL}/clients?error=invalid_amount", status_code=303)

    old_bal = round(float(client.wallet_balance or 0.0), 2)
    if action == "credit":
        new_bal = round(old_bal + amount, 2)
        txn_amt = amount
        adj_type = "Credit"
    else:
        new_bal = round(max(0.0, old_bal - amount), 2)
        txn_amt = -amount
        adj_type = "Debit"

    client.wallet_balance = new_bal

    # Record immutable audit transaction
    txn = WalletTransaction(
        user_id=client.id,
        transaction_type="adjustment",
        amount=txn_amt,
        balance_after=new_bal,
        reference=f"ADJ-{current_admin.id}-{int(datetime.now(timezone.utc).timestamp())}",
        description=f"Admin Manual {adj_type}: {reason.strip()}",
        created_at=datetime.now(timezone.utc)
    )
    db.add(txn)
    db.commit()

    log_admin_action(
        db=db,
        admin_id=current_admin.id,
        action="WALLET_ADJUSTMENT",
        target=f"Client #{client.id} ({client.email})",
        details=f"{adj_type} of ${amount:.2f}. Balance: ${old_bal:.2f} -> ${new_bal:.2f}. Reason: {reason.strip()}"
    )

    return RedirectResponse(url=f"{ADMIN_SECRET_URL}/clients?success=wallet_adjusted", status_code=303)

@router.post("/clients/{client_id}/xp-adjust")
async def admin_adjust_client_xp(
    client_id: int,
    action: str = Form("grant"),
    amount: int = Form(...),
    reason: str = Form("Admin manual XP adjustment"),
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Allows administrators to manually award or deduct customer XP and elevate VIP loyalty tiers."""
    client = db.query(User).filter(User.id == client_id, User.role == UserRole.CUSTOMER.value).first()
    if not client:
        raise HTTPException(status_code=404, detail="Customer not found")

    amount = abs(int(amount))
    if amount <= 0:
        return RedirectResponse(url=f"{ADMIN_SECRET_URL}/clients?error=invalid_xp_amount", status_code=303)

    old_xp = int(client.xp_points or 0)
    delta = amount if action == "grant" else -amount
    new_xp = loyalty_service.admin_adjust_xp(client, delta, db)
    adj_type = "Granted" if action == "grant" else "Deducted"

    log_admin_action(
        db=db,
        admin_id=current_admin.id,
        action="XP_ADJUSTMENT",
        target=f"Client #{client.id} ({client.email})",
        details=f"{adj_type} {amount} XP. XP: {old_xp} -> {new_xp} (Tier: {client.loyalty_tier['name']}). Reason: {reason.strip()}"
    )

    return RedirectResponse(url=f"{ADMIN_SECRET_URL}/clients?success=xp_adjusted", status_code=303)

# ==========================================
# 5. ADMIN ACCOUNTS & RBAC CONTROLLER
# ==========================================

@router.get("/admins", response_class=HTMLResponse)
async def admin_accounts_page(
    request: Request, 
    current_admin: User = Depends(require_super_admin), 
    db: Session = Depends(get_db)
):
    admins = db.query(User).filter(User.role.in_([UserRole.ADMIN.value, UserRole.SUPER_ADMIN.value])).all()
    audit_logs = db.query(AdminAuditLog).order_by(AdminAuditLog.id.desc()).limit(20).all()
    
    return templates.TemplateResponse(
        request=request,
        name="admin/admins.html",
        context={
            "current_admin": current_admin,
            "admin_secret_url": ADMIN_SECRET_URL,
            "admins": admins,
            "audit_logs": audit_logs
        }
    )

@router.post("/admins/add")
async def admin_create_subadmin(
    full_name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    role: str = Form(UserRole.ADMIN.value),
    current_admin: User = Depends(require_super_admin),
    db: Session = Depends(get_db)
):
    clean_email = email.strip().lower()
    existing = db.query(User).filter(User.email == clean_email).first()
    if existing:
        return RedirectResponse(url=f"{ADMIN_SECRET_URL}/admins?error=email_exists", status_code=303)

    new_admin = User(
        full_name=full_name.strip(),
        email=clean_email,
        password_hash=hash_password(password),
        role=role if role in [UserRole.ADMIN.value, UserRole.SUPER_ADMIN.value] else UserRole.ADMIN.value,
        is_active=True
    )
    db.add(new_admin)
    db.commit()

    log_admin_action(db, current_admin.id, "ADMIN_CREATE", f"Created new admin account: {new_admin.email} (Role: {new_admin.role})")
    return RedirectResponse(url=f"{ADMIN_SECRET_URL}/admins?success=created", status_code=303)

@router.post("/admins/{admin_id}/update")
async def admin_update_account(
    admin_id: int,
    full_name: str = Form(...),
    email: str = Form(...),
    role: str = Form(UserRole.ADMIN.value),
    new_password: str = Form(""),
    current_admin: User = Depends(require_super_admin),
    db: Session = Depends(get_db)
):
    target = db.query(User).filter(
        User.id == admin_id,
        User.role.in_([UserRole.ADMIN.value, UserRole.SUPER_ADMIN.value])
    ).first()
    if not target:
        return RedirectResponse(url=f"{ADMIN_SECRET_URL}/admins?error=not_found", status_code=303)

    clean_email = email.strip().lower()
    conflict = db.query(User).filter(User.email == clean_email, User.id != admin_id).first()
    if conflict:
        return RedirectResponse(url=f"{ADMIN_SECRET_URL}/admins?error=email_exists", status_code=303)

    # Demoting last super admin safeguard
    if target.role == UserRole.SUPER_ADMIN.value and role != UserRole.SUPER_ADMIN.value:
        super_count = db.query(User).filter(User.role == UserRole.SUPER_ADMIN.value).count()
        if super_count <= 1:
            return RedirectResponse(url=f"{ADMIN_SECRET_URL}/admins?error=last_super_admin", status_code=303)

    target.full_name = full_name.strip()
    target.email = clean_email
    if role in [UserRole.ADMIN.value, UserRole.SUPER_ADMIN.value]:
        target.role = role

    pwd_changed = False
    if new_password and new_password.strip():
        target.password_hash = hash_password(new_password.strip())
        pwd_changed = True

    db.commit()
    log_admin_action(
        db=db,
        admin_id=current_admin.id,
        action="ADMIN_UPDATE",
        target=target.email,
        details=f"Updated credentials (Role: {target.role}, Password changed: {pwd_changed})"
    )
    return RedirectResponse(url=f"{ADMIN_SECRET_URL}/admins?success=updated", status_code=303)

@router.post("/admins/{admin_id}/delete")
async def admin_delete_account(
    admin_id: int,
    current_admin: User = Depends(require_super_admin),
    db: Session = Depends(get_db)
):
    target = db.query(User).filter(
        User.id == admin_id,
        User.role.in_([UserRole.ADMIN.value, UserRole.SUPER_ADMIN.value])
    ).first()
    if not target:
        return JSONResponse({"success": False, "error": "Admin account not found."}, status_code=404)

    # Cannot delete self
    if target.id == current_admin.id:
        return JSONResponse({
            "success": False,
            "error": "Self-deletion prohibited. You cannot delete your currently active administrator account."
        }, status_code=400)

    # Cannot delete sole remaining super admin
    if target.role == UserRole.SUPER_ADMIN.value:
        super_count = db.query(User).filter(User.role == UserRole.SUPER_ADMIN.value).count()
        if super_count <= 1:
            return JSONResponse({
                "success": False,
                "error": "Cannot delete the sole remaining Super Administrator."
            }, status_code=400)

    deleted_email = target.email
    db.delete(target)
    db.commit()

    log_admin_action(db, current_admin.id, "ADMIN_DELETE", deleted_email, "Administrator account removed permanently")
    return JSONResponse({"success": True, "message": f"Administrator '{deleted_email}' successfully removed."})

# ==========================================
# 6. ORDER INSPECTION & STATUS
# ==========================================

@router.get("/order/{order_id}")
async def get_order_details(
    order_id: int, 
    current_admin: User = Depends(require_admin), 
    db: Session = Depends(get_db)
):
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        return JSONResponse({"success": False, "error": "Order not found."}, status_code=404)

    items_data = []
    for item in order.items:
        # Get live assigned keys from relationship or fallback to delivered_keys_json
        item_keys = [k.key_code for k in order.assigned_keys if k.product_id == item.product_id]
        if not item_keys and item.delivered_keys_json:
            try:
                item_keys = json.loads(item.delivered_keys_json)
            except Exception:
                item_keys = []

        items_data.append({
            "product_title": item.product.title,
            "platform": item.product.platform,
            "unit_price": item.unit_price,
            "cost_price": item.cost_price,
            "quantity": item.quantity,
            "keys": item_keys
        })

    return JSONResponse({
        "success": True,
        "order": {
            "id": order.id,
            "order_number": order.order_number,
            "customer_name": order.user.full_name,
            "customer_email": order.user.email,
            "created_at": order.created_at.strftime("%Y-%m-%d %H:%M UTC"),
            "payment_method": order.payment_method,
            "status": order.status,
            "total_amount": order.total_amount,
            "total_cost": order.total_cost,
            "profit": order.profit,
            "receipt_file_url": order.receipt_file_url,
            "client_notes": order.client_notes,
            "assigned_keys_count": len(order.assigned_keys),
            "items": items_data
        }
    })

@router.post("/order/{order_id}/status")
async def update_order_status(
    order_id: int, 
    new_status: str = Form(...),
    restock_keys: str = Form("true"),
    current_admin: User = Depends(require_admin), 
    db: Session = Depends(get_db)
):
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        return JSONResponse({"success": False, "error": "Order not found."}, status_code=404)

    clean_status = new_status.strip().lower()
    valid_statuses = [
        OrderStatus.COMPLETED.value, 
        OrderStatus.PENDING.value, 
        OrderStatus.REFUNDED.value, 
        OrderStatus.CANCELLED.value, 
        OrderStatus.PROCESSING.value
    ]
    if clean_status not in valid_statuses:
        return JSONResponse({"success": False, "error": f"Invalid status '{clean_status}'."}, status_code=400)

    old_status = order.status
    should_restock = str(restock_keys).strip().lower() in ["true", "1", "yes", "on"]
    restocked_count = 0
    assigned_count = 0
    now_utc = datetime.now(timezone.utc)

    # 1. Transitioning to CANCELLED or REFUNDED
    if clean_status in [OrderStatus.CANCELLED.value, OrderStatus.REFUNDED.value]:
        order.profit = 0.0
        if should_restock:
            for key in list(order.assigned_keys):
                key.status = KeyStatus.AVAILABLE.value
                key.order_id = None
                key.redeemed_at = None
                restocked_count += 1
            for item in order.items:
                item.delivered_keys_json = json.dumps([])

    # 2. Transitioning to PENDING
    elif clean_status == OrderStatus.PENDING.value:
        order.profit = 0.0
        if should_restock:
            for key in list(order.assigned_keys):
                key.status = KeyStatus.AVAILABLE.value
                key.order_id = None
                key.redeemed_at = None
                restocked_count += 1
            for item in order.items:
                item.delivered_keys_json = json.dumps([])

    # 3. Transitioning to COMPLETED
    elif clean_status == OrderStatus.COMPLETED.value:
        order.profit = round(order.total_amount - order.total_cost, 2)
        # Verify / fulfill key assignment for items that lack keys
        for item in order.items:
            existing_keys = [k for k in order.assigned_keys if k.product_id == item.product_id]
            needed = item.quantity - len(existing_keys)
            if needed > 0:
                available_keys = db.query(DigitalKey).filter(
                    DigitalKey.product_id == item.product_id,
                    DigitalKey.status == KeyStatus.AVAILABLE.value
                ).limit(needed).all()

                if len(available_keys) < needed:
                    db.rollback()
                    return JSONResponse({
                        "success": False,
                        "error": f"Cannot complete order: Insufficient keys for '{item.product.title}'. Needed {needed}, available {len(available_keys)}."
                    }, status_code=400)

                for k in available_keys:
                    k.status = KeyStatus.SOLD.value
                    k.order_id = order.id
                    k.redeemed_at = now_utc
                    assigned_count += 1

            # Refresh delivered_keys_json
            item_keys = [k.key_code for k in order.assigned_keys if k.product_id == item.product_id]
            item.delivered_keys_json = json.dumps(item_keys)

        # Ensure all assigned keys are marked as SOLD
        for k in order.assigned_keys:
            k.status = KeyStatus.SOLD.value
            if not k.redeemed_at:
                k.redeemed_at = now_utc

    order.status = clean_status
    db.commit()

    # Automated Email Receipt Dispatch & Referral Reward upon Order Completion
    email_dispatched = False
    if clean_status == OrderStatus.COMPLETED.value:
        try:
            send_order_receipt_email(order, db)
            email_dispatched = True
        except Exception as ex:
            print(f"[Admin] Automatic receipt email error: {ex}")
        try:
            wallet_service.award_referral_reward(order, db)
        except Exception as ex:
            print(f"[Admin] Automatic referral commission error: {ex}")
        try:
            loyalty_service.award_order_xp(order, db)
        except Exception as ex:
            print(f"[Admin] Automatic loyalty XP award error: {ex}")

    detail_msg = f"Order {order.order_number} status updated: {old_status.upper()} -> {clean_status.upper()}."
    if email_dispatched:
        detail_msg += " Official email receipt with license keys dispatched to customer."
    if restocked_count > 0:
        detail_msg += f" {restocked_count} license key(s) returned to available stock."
    if assigned_count > 0:
        detail_msg += f" {assigned_count} license key(s) allocated from inventory."

    log_admin_action(
        db=db,
        admin_id=current_admin.id,
        action="ORDER_STATUS_UPDATE",
        target=order.order_number,
        details=detail_msg
    )

    return JSONResponse({
        "success": True,
        "message": detail_msg,
        "order": {
            "id": order.id,
            "order_number": order.order_number,
            "status": order.status,
            "profit": order.profit,
            "restocked_count": restocked_count,
            "assigned_count": assigned_count,
            "email_dispatched": email_dispatched
        }
    })

@router.post("/order/{order_id}/send-receipt-email")
async def admin_send_receipt_email(
    order_id: int,
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        return JSONResponse({"success": False, "error": "Order not found."}, status_code=404)

    res = send_order_receipt_email(order, db)
    log_admin_action(
        db=db,
        admin_id=current_admin.id,
        action="EMAIL_DISPATCH",
        target=order.order_number,
        details=f"Receipt email dispatched to {order.user.email} (SMTP sent: {res.get('smtp_sent', False)})"
    )
    return JSONResponse(res)

@router.get("/order/{order_id}/invoice")
async def admin_download_order_invoice(
    order_id: int,
    download: bool = True,
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Generates and returns the official PDF invoice for the specified order."""
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found.")

    pdf_bytes = generate_order_invoice_pdf(order, db, cache_to_disk=True)
    disposition = "attachment" if download else "inline"
    filename = f"Invoice-{order.order_number}.pdf"

    log_admin_action(
        db=db,
        admin_id=current_admin.id,
        action="INVOICE_DOWNLOAD",
        target=order.order_number,
        details=f"Generated and downloaded PDF invoice for {order.order_number}"
    )

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'{disposition}; filename="{filename}"',
            "Cache-Control": "no-cache"
        }
    )

# ==========================================
# 7. COUPONS & PROMOTIONS CONTROLLER
# ==========================================

@router.get("/coupons", response_class=HTMLResponse)
async def admin_coupons_page(
    request: Request,
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    coupons = db.query(Coupon).order_by(Coupon.id.desc()).all()
    return templates.TemplateResponse(
        request=request,
        name="admin/coupons.html",
        context={
            "current_admin": current_admin,
            "admin_secret_url": ADMIN_SECRET_URL,
            "coupons": coupons
        }
    )

@router.post("/coupons/add")
async def admin_add_coupon(
    code: str = Form(...),
    discount_type: str = Form("percent"),
    discount_value: float = Form(...),
    min_spend: float = Form(0.0),
    max_uses: int = Form(500),
    description: str = Form(""),
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    clean_code = code.strip().upper()
    existing = db.query(Coupon).filter(Coupon.code == clean_code).first()
    if existing:
        return RedirectResponse(url=f"{ADMIN_SECRET_URL}/coupons?error=exists", status_code=303)

    coupon = Coupon(
        code=clean_code,
        discount_type=discount_type if discount_type in ["percent", "fixed"] else "percent",
        discount_value=abs(discount_value),
        min_spend=max(0.0, min_spend),
        max_uses=max(1, max_uses),
        times_used=0,
        is_active=True,
        description=description.strip()
    )
    db.add(coupon)
    db.commit()

    log_admin_action(db, current_admin.id, "COUPON_CREATE", clean_code, f"Created {discount_value}{'%' if discount_type=='percent' else '$'} coupon")
    return RedirectResponse(url=f"{ADMIN_SECRET_URL}/coupons?success=created", status_code=303)

@router.post("/coupons/{coupon_id}/toggle")
async def admin_toggle_coupon(
    coupon_id: int,
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    coupon = db.query(Coupon).filter(Coupon.id == coupon_id).first()
    if not coupon:
        return JSONResponse({"success": False, "error": "Coupon not found"}, status_code=404)

    coupon.is_active = not coupon.is_active
    db.commit()

    state = "ENABLED" if coupon.is_active else "DISABLED"
    log_admin_action(db, current_admin.id, "COUPON_TOGGLE", coupon.code, f"Status changed to {state}")
    return JSONResponse({"success": True, "is_active": coupon.is_active, "code": coupon.code})

@router.post("/coupons/{coupon_id}/delete")
async def admin_delete_coupon(
    coupon_id: int,
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    coupon = db.query(Coupon).filter(Coupon.id == coupon_id).first()
    if not coupon:
        return JSONResponse({"success": False, "error": "Coupon not found"}, status_code=404)

    code = coupon.code
    db.delete(coupon)
    db.commit()

    log_admin_action(db, current_admin.id, "COUPON_DELETE", code, "Coupon permanently deleted")
    return JSONResponse({"success": True, "message": f"Coupon {code} deleted."})

# ==========================================
# 7.2. GIFT VOUCHERS & STORE CREDIT CONTROLLER
# ==========================================

@router.get("/vouchers", response_class=HTMLResponse)
async def admin_vouchers_page(
    request: Request,
    q: str = None,
    status: str = "all",
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    query = db.query(GiftVoucher).order_by(GiftVoucher.id.desc())

    if q and q.strip():
        search_term = f"%{q.strip().upper()}%"
        query = query.filter((GiftVoucher.code.ilike(search_term)) | (GiftVoucher.batch_name.ilike(f"%{q.strip()}%")))

    if status == "active":
        query = query.filter(GiftVoucher.is_redeemed == False)
    elif status == "redeemed":
        query = query.filter(GiftVoucher.is_redeemed == True)

    vouchers = query.all()

    # Precalculate summary metrics
    all_vouchers = db.query(GiftVoucher).all()
    total_count = len(all_vouchers)
    active_count = sum(1 for v in all_vouchers if not v.is_redeemed)
    redeemed_count = sum(1 for v in all_vouchers if v.is_redeemed)
    total_value = sum(v.amount for v in all_vouchers)
    redeemed_value = sum(v.amount for v in all_vouchers if v.is_redeemed)

    # Distinct batches
    batches = db.query(GiftVoucher.batch_name).filter(GiftVoucher.batch_name.isnot(None)).distinct().all()
    batch_names = [b[0] for b in batches if b[0]]

    return templates.TemplateResponse(
        request=request,
        name="admin/vouchers.html",
        context={
            "current_admin": current_admin,
            "admin_secret_url": ADMIN_SECRET_URL,
            "vouchers": vouchers,
            "total_count": total_count,
            "active_count": active_count,
            "redeemed_count": redeemed_count,
            "total_value": round(total_value, 2),
            "redeemed_value": round(redeemed_value, 2),
            "batch_names": batch_names,
            "search_query": q or "",
            "status_filter": status
        }
    )

@router.post("/vouchers/create")
async def admin_create_voucher(
    request: Request,
    amount: float = Form(...),
    code: str = Form(None),
    batch_name: str = Form(None),
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    import uuid
    clean_code = (code or "").strip().upper()
    if not clean_code:
        clean_code = f"NXUS-CARD-{uuid.uuid4().hex[:4].upper()}-{uuid.uuid4().hex[:4].upper()}"

    existing = db.query(GiftVoucher).filter(GiftVoucher.code == clean_code).first()
    if existing:
        return RedirectResponse(url=f"{ADMIN_SECRET_URL}/vouchers?error=code_exists", status_code=303)

    amount = round(abs(float(amount)), 2)
    if amount <= 0:
        return RedirectResponse(url=f"{ADMIN_SECRET_URL}/vouchers?error=invalid_amount", status_code=303)

    voucher = GiftVoucher(
        code=clean_code,
        amount=amount,
        is_redeemed=False,
        batch_name=batch_name.strip() if batch_name and batch_name.strip() else None,
        created_at=datetime.now(timezone.utc)
    )
    db.add(voucher)
    db.commit()

    log_admin_action(db, current_admin.id, "VOUCHER_CREATE", clean_code, f"Created ${amount:.2f} gift voucher.")
    return RedirectResponse(url=f"{ADMIN_SECRET_URL}/vouchers?success=created", status_code=303)

@router.post("/vouchers/generate-batch")
async def admin_generate_voucher_batch(
    request: Request,
    count: int = Form(10),
    amount: float = Form(...),
    batch_name: str = Form("Promotional Campaign"),
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    import uuid
    clean_count = max(1, min(100, int(count)))
    amount = round(abs(float(amount)), 2)
    clean_batch = batch_name.strip() if batch_name else "Batch Promo"

    created_vouchers = []
    for _ in range(clean_count):
        code = f"NXUS-CARD-{uuid.uuid4().hex[:4].upper()}-{uuid.uuid4().hex[:4].upper()}"
        v = GiftVoucher(
            code=code,
            amount=amount,
            is_redeemed=False,
            batch_name=clean_batch,
            created_at=datetime.now(timezone.utc)
        )
        created_vouchers.append(v)

    db.add_all(created_vouchers)
    db.commit()

    log_admin_action(db, current_admin.id, "VOUCHER_BATCH_GENERATE", clean_batch, f"Generated {clean_count} vouchers of ${amount:.2f} each (Total: ${clean_count * amount:.2f}).")
    return RedirectResponse(url=f"{ADMIN_SECRET_URL}/vouchers?success=batch_generated&count={clean_count}", status_code=303)

@router.post("/vouchers/{voucher_id}/delete")
async def admin_delete_voucher(
    voucher_id: int,
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    voucher = db.query(GiftVoucher).filter(GiftVoucher.id == voucher_id).first()
    if not voucher:
        return JSONResponse({"success": False, "error": "Voucher not found"}, status_code=404)

    if voucher.is_redeemed:
        return JSONResponse({"success": False, "error": "Cannot delete already redeemed vouchers to preserve accounting audit records."}, status_code=400)

    code = voucher.code
    db.delete(voucher)
    db.commit()

    log_admin_action(db, current_admin.id, "VOUCHER_DELETE", code, "Unredeemed voucher permanently deleted")
    return JSONResponse({"success": True, "message": f"Voucher {code} deleted."})

@router.get("/export/vouchers.csv")
async def export_vouchers_csv(
    batch: str = None,
    current_admin: User = Depends(require_super_admin),
    db: Session = Depends(get_db)
):
    query = db.query(GiftVoucher).order_by(GiftVoucher.id.desc())
    if batch:
        query = query.filter(GiftVoucher.batch_name == batch)
    vouchers = query.all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Voucher ID", "Code", "Amount ($)", "Status", "Batch Name", "Created Date", "Redeemed Date", "Redeemed By Customer"
    ])

    for v in vouchers:
        redeemer = v.redeemed_by.email if v.redeemed_by else "None"
        writer.writerow([
            v.id,
            v.code,
            f"{v.amount:.2f}",
            "REDEEMED" if v.is_redeemed else "ACTIVE",
            v.batch_name or "N/A",
            v.created_at.strftime("%Y-%m-%d %H:%M:%S") if v.created_at else "",
            v.redeemed_at.strftime("%Y-%m-%d %H:%M:%S") if v.redeemed_at else "",
            redeemer
        ])

    log_admin_action(db, current_admin.id, "DATA_EXPORT", "vouchers.csv", f"Exported {len(vouchers)} gift vouchers")
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=nexus_vouchers_export_{int(datetime.now().timestamp())}.csv"}
    )

# ==========================================
# 7.5. CUSTOMER SUPPORT TICKETS CONTROLLER
# ==========================================

@router.get("/tickets", response_class=HTMLResponse)
async def admin_tickets_page(
    request: Request,
    status: str = "all",
    priority: str = "all",
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    query = db.query(Ticket).order_by(Ticket.updated_at.desc())

    if status and status != "all":
        query = query.filter(Ticket.status == status)
    if priority and priority != "all":
        query = query.filter(Ticket.priority == priority)

    tickets = query.all()

    # KPI Metrics
    all_tickets = db.query(Ticket).all()
    open_count = sum(1 for t in all_tickets if t.status == TicketStatus.OPEN.value)
    in_progress_count = sum(1 for t in all_tickets if t.status == TicketStatus.IN_PROGRESS.value)
    waiting_client_count = sum(1 for t in all_tickets if t.status == TicketStatus.WAITING_CLIENT.value)
    resolved_count = sum(1 for t in all_tickets if t.status in [TicketStatus.RESOLVED.value, TicketStatus.CLOSED.value])
    urgent_count = sum(1 for t in all_tickets if t.priority == "urgent" and t.status not in [TicketStatus.RESOLVED.value, TicketStatus.CLOSED.value])

    return templates.TemplateResponse(
        request=request,
        name="admin/tickets.html",
        context={
            "current_admin": current_admin,
            "tickets": tickets,
            "current_status": status,
            "current_priority": priority,
            "open_count": open_count,
            "in_progress_count": in_progress_count,
            "waiting_client_count": waiting_client_count,
            "resolved_count": resolved_count,
            "urgent_count": urgent_count,
            "admin_secret_url": ADMIN_SECRET_URL,
            "success": request.query_params.get("success"),
            "error": request.query_params.get("error")
        }
    )

@router.get("/tickets/{ticket_id}", response_class=HTMLResponse)
async def admin_ticket_detail_page(
    ticket_id: int,
    request: Request,
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if not ticket:
        return RedirectResponse(url=f"{ADMIN_SECRET_URL}/tickets?error=Ticket+not+found", status_code=303)

    return templates.TemplateResponse(
        request=request,
        name="admin/ticket_detail.html",
        context={
            "current_admin": current_admin,
            "ticket": ticket,
            "admin_secret_url": ADMIN_SECRET_URL,
            "success": request.query_params.get("success"),
            "error": request.query_params.get("error")
        }
    )

@router.post("/tickets/{ticket_id}/reply")
async def admin_ticket_reply(
    ticket_id: int,
    message: str = Form(...),
    new_status: str = Form("waiting_client"),
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if not ticket:
        return RedirectResponse(url=f"{ADMIN_SECRET_URL}/tickets?error=Ticket+not+found", status_code=303)

    clean_message = message.strip()
    if not clean_message:
        return RedirectResponse(url=f"{ADMIN_SECRET_URL}/tickets/{ticket_id}?error=Message+cannot+be+empty", status_code=303)

    msg = TicketMessage(
        ticket_id=ticket.id,
        sender_id=current_admin.id,
        sender_role=current_admin.role,
        message=clean_message,
        created_at=datetime.now(timezone.utc)
    )
    db.add(msg)

    old_status = ticket.status
    if new_status in [s.value for s in TicketStatus]:
        ticket.status = new_status
    ticket.updated_at = datetime.now(timezone.utc)
    db.commit()

    log_admin_action(
        db, current_admin.id, "TICKET_REPLY", ticket.ticket_number,
        f"Replied to inquiry {ticket.ticket_number} (status: {old_status} -> {ticket.status})"
    )
    return RedirectResponse(
        url=f"{ADMIN_SECRET_URL}/tickets/{ticket_id}?success=Staff+reply+posted+successfully",
        status_code=303
    )

@router.post("/tickets/{ticket_id}/status")
async def admin_ticket_status_update(
    ticket_id: int,
    status: str = Form(...),
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if not ticket:
        return JSONResponse({"success": False, "error": "Ticket not found"}, status_code=404)

    if status in [s.value for s in TicketStatus]:
        old_status = ticket.status
        ticket.status = status
        ticket.updated_at = datetime.now(timezone.utc)
        db.commit()
        log_admin_action(
            db, current_admin.id, "TICKET_STATUS_UPDATE", ticket.ticket_number,
            f"Status changed from {old_status} to {status}"
        )
        return JSONResponse({"success": True, "ticket_number": ticket.ticket_number, "status": ticket.status})

    return JSONResponse({"success": False, "error": "Invalid status value"}, status_code=400)

# ==========================================
# 8. STORE SETTINGS & ANNOUNCEMENTS CONTROLLER
# ==========================================

@router.get("/settings", response_class=HTMLResponse)
async def admin_settings_page(
    request: Request,
    current_admin: User = Depends(require_super_admin),
    db: Session = Depends(get_db)
):
    settings_records = db.query(SiteSetting).all()
    settings_dict = {s.key: s.value for s in settings_records}

    return templates.TemplateResponse(
        request=request,
        name="admin/settings.html",
        context={
            "current_admin": current_admin,
            "admin_secret_url": ADMIN_SECRET_URL,
            "settings": settings_dict
        }
    )

@router.post("/settings/update")
async def admin_update_settings(
    store_name: str = Form("NEXUS KEYS"),
    tagline: str = Form(""),
    support_email: str = Form(""),
    currency_symbol: str = Form("$"),
    announcement_banner_text: str = Form(""),
    announcement_banner_active: str = Form("false"),
    maintenance_mode: str = Form("false"),
    smtp_host: str = Form(""),
    smtp_port: str = Form("587"),
    smtp_user: str = Form(""),
    smtp_password: str = Form(""),
    smtp_from_email: str = Form(""),
    smtp_use_tls: str = Form("true"),
    current_admin: User = Depends(require_super_admin),
    db: Session = Depends(get_db)
):
    updates = {
        "store_name": store_name.strip(),
        "tagline": tagline.strip(),
        "support_email": support_email.strip(),
        "currency_symbol": currency_symbol.strip(),
        "announcement_banner_text": announcement_banner_text.strip(),
        "announcement_banner_active": "true" if announcement_banner_active.lower() in ["true", "1", "on"] else "false",
        "maintenance_mode": "true" if maintenance_mode.lower() in ["true", "1", "on"] else "false",
        "smtp_host": smtp_host.strip(),
        "smtp_port": smtp_port.strip() if smtp_port.strip() else "587",
        "smtp_user": smtp_user.strip(),
        "smtp_from_email": smtp_from_email.strip(),
        "smtp_use_tls": "true" if smtp_use_tls.lower() in ["true", "1", "on"] else "false"
    }

    # Only update password if a new one was entered
    if smtp_password.strip():
        updates["smtp_password"] = smtp_password.strip()

    for key, val in updates.items():
        record = db.query(SiteSetting).filter(SiteSetting.key == key).first()
        if record:
            record.value = val
        else:
            db.add(SiteSetting(key=key, value=val, description=f"Store configuration: {key}"))

    db.commit()
    log_admin_action(db, current_admin.id, "SETTINGS_UPDATE", "Store Settings", "Updated storefront branding, announcements & SMTP config")
    return RedirectResponse(url=f"{ADMIN_SECRET_URL}/settings?success=updated", status_code=303)

@router.post("/settings/test-email")
async def admin_test_email(
    test_email: str = Form(...),
    current_admin: User = Depends(require_super_admin),
    db: Session = Depends(get_db)
):
    res = send_test_email(test_email.strip(), db)
    log_admin_action(
        db=db,
        admin_id=current_admin.id,
        action="SMTP_TEST",
        target=test_email.strip(),
        details=f"Test email result: {res.get('success', False)}"
    )
    return JSONResponse(res)

# ==========================================
# 9. CSV DATA EXPORT ENGINE
# ==========================================

@router.get("/export/orders.csv")
async def export_orders_csv(
    current_admin: User = Depends(require_super_admin),
    db: Session = Depends(get_db)
):
    orders = db.query(Order).order_by(Order.id.desc()).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Order Number", "Date (UTC)", "Customer Name", "Customer Email",
        "Payment Method", "Total Paid ($)", "Wholesale Cost ($)", "Net Profit ($)", "Status"
    ])

    for o in orders:
        writer.writerow([
            o.order_number,
            o.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            o.user.full_name if o.user else "Unknown",
            o.user.email if o.user else "Unknown",
            o.payment_method,
            f"{o.total_amount:.2f}",
            f"{o.total_cost:.2f}",
            f"{o.profit:.2f}",
            o.status.upper()
        ])

    log_admin_action(db, current_admin.id, "DATA_EXPORT", "orders.csv", f"Exported {len(orders)} orders")
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=nexus_orders_export_{int(datetime.now().timestamp())}.csv"}
    )

@router.get("/export/inventory.csv")
async def export_inventory_csv(
    current_admin: User = Depends(require_super_admin),
    db: Session = Depends(get_db)
):
    keys = db.query(DigitalKey).order_by(DigitalKey.id.desc()).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Key ID", "Product Title", "Platform", "Region",
        "Key Code", "Status", "Order ID", "Created Date", "Redeemed Date"
    ])

    for k in keys:
        writer.writerow([
            k.id,
            k.product.title if k.product else "Unknown",
            k.product.platform if k.product else "N/A",
            k.product.region if k.product else "N/A",
            k.key_code,
            k.status.upper(),
            k.order_id if k.order_id else "None",
            k.created_at.strftime("%Y-%m-%d %H:%M:%S") if k.created_at else "",
            k.redeemed_at.strftime("%Y-%m-%d %H:%M:%S") if k.redeemed_at else ""
        ])

    log_admin_action(db, current_admin.id, "DATA_EXPORT", "inventory.csv", f"Exported {len(keys)} license keys")
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=nexus_keys_inventory_{int(datetime.now().timestamp())}.csv"}
    )

@router.get("/export/clients.csv")
async def export_clients_csv(
    current_admin: User = Depends(require_super_admin),
    db: Session = Depends(get_db)
):
    clients = db.query(User).filter(User.role == UserRole.CUSTOMER.value).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Client ID", "Full Name", "Email", "Role", "Completed Orders", "Lifetime Spend ($)", "Wallet Balance ($)", "XP Points", "Loyalty Tier", "Account Status", "Registered Date"
    ])

    for c in clients:
        spend = sum(o.total_amount for o in c.orders if o.status == OrderStatus.COMPLETED.value)
        completed_orders = sum(1 for o in c.orders if o.status == OrderStatus.COMPLETED.value)
        writer.writerow([
            c.id,
            c.full_name,
            c.email,
            c.role,
            completed_orders,
            f"{spend:.2f}",
            f"{(c.wallet_balance or 0.0):.2f}",
            c.xp_points or 0,
            c.loyalty_tier["name"],
            "ACTIVE" if c.is_active else "BANNED",
            c.created_at.strftime("%Y-%m-%d %H:%M:%S") if c.created_at else ""
        ])

    log_admin_action(db, current_admin.id, "DATA_EXPORT", "clients.csv", f"Exported {len(clients)} clients")
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=nexus_clients_export_{int(datetime.now().timestamp())}.csv"}
    )

# ==========================================
# 10. REVIEWS & RATINGS MODERATION
# ==========================================

@router.get("/reviews", response_class=HTMLResponse)
async def admin_reviews_page(
    request: Request,
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    reviews = db.query(ProductReview).order_by(ProductReview.id.desc()).all()
    return templates.TemplateResponse(
        request=request,
        name="admin/reviews.html",
        context={
            "current_admin": current_admin,
            "admin_secret_url": ADMIN_SECRET_URL,
            "reviews": reviews
        }
    )

@router.post("/reviews/{review_id}/toggle")
async def admin_toggle_review(
    review_id: int,
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    review = db.query(ProductReview).filter(ProductReview.id == review_id).first()
    if not review:
        return JSONResponse({"success": False, "error": "Review not found"}, status_code=404)

    review.is_approved = not review.is_approved
    db.commit()

    log_admin_action(db, current_admin.id, "REVIEW_MODERATE", f"Review #{review.id}", f"Approved: {review.is_approved}")
    return JSONResponse({"success": True, "is_approved": review.is_approved})

@router.post("/reviews/{review_id}/delete")
async def admin_delete_review(
    review_id: int,
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    review = db.query(ProductReview).filter(ProductReview.id == review_id).first()
    if not review:
        return JSONResponse({"success": False, "error": "Review not found"}, status_code=404)

    db.delete(review)
    db.commit()

    log_admin_action(db, current_admin.id, "REVIEW_DELETE", f"Review #{review_id}", "Review deleted permanently")
    return JSONResponse({"success": True, "message": "Review deleted."})

@router.get("/logout")
async def admin_logout(request: Request):
    response = RedirectResponse(url=f"{ADMIN_SECRET_URL}/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response
