from fastapi import APIRouter, Request, Depends, HTTPException, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from pathlib import Path
from app.database import get_db
from app.models import Product, KeyStatus, Coupon
from app.security import get_current_user_from_request

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))

# Standard valid coupons for digital shoppers
PROMO_CODES = {
    "GAMER10": {"type": "percent", "value": 10.0, "description": "10% Gamers Discount"},
    "WELCOME5": {"type": "fixed", "value": 5.0, "description": "$5 Off Welcome Voucher"},
    "NEXUS2026": {"type": "percent", "value": 15.0, "description": "15% Special Launch Promo"}
}

def get_session_cart(request: Request) -> dict:
    """Retrieve or initialize cart from session."""
    return request.session.get("cart", {}) if hasattr(request, "session") else {}

@router.get("/cart", response_class=HTMLResponse)
async def cart_page(request: Request, db: Session = Depends(get_db)):
    """Render full shopping cart view."""
    cart = request.cookies.get("cart_data", "")
    current_user = get_current_user_from_request(request, db)
    return templates.TemplateResponse(
        request=request,
        name="store/cart.html",
        context={
            "current_user": current_user
        }
    )

@router.post("/api/cart/validate")
async def validate_cart_items(request: Request, payload: dict, db: Session = Depends(get_db)):
    """
    Validates cart item IDs, verifies real-time stock availability,
    and returns detailed items, subtotal, discount, and grand total.
    """
    items_in = payload.get("items", [])  # list of {id, qty}
    coupon_code = payload.get("coupon", "").strip().upper()

    validated_items = []
    subtotal = 0.0

    for item in items_in:
        p_id = item.get("id")
        qty = max(1, int(item.get("qty", 1)))
        product = db.query(Product).filter(Product.id == p_id, Product.is_active == True).first()
        
        if not product:
            continue

        available_stock = product.available_keys_count
        actual_qty = min(qty, max(1, available_stock)) if available_stock > 0 else 0

        item_total = round(product.price * actual_qty, 2)
        subtotal += item_total

        validated_items.append({
            "id": product.id,
            "title": product.title,
            "slug": product.slug,
            "price": product.price,
            "image_url": product.image_url,
            "platform": product.platform,
            "region": product.region,
            "qty": actual_qty,
            "available_stock": available_stock,
            "item_total": item_total,
            "in_stock": available_stock > 0
        })

    # Calculate discount from database or fallback to static promos
    discount_amount = 0.0
    applied_coupon = None

    if coupon_code:
        db_coupon = db.query(Coupon).filter(
            Coupon.code == coupon_code,
            Coupon.is_active == True
        ).first()

        if db_coupon:
            if subtotal >= db_coupon.min_spend and db_coupon.times_used < db_coupon.max_uses:
                applied_coupon = {"code": db_coupon.code, "description": db_coupon.description or f"{db_coupon.discount_value}% Discount"}
                if db_coupon.discount_type == "percent":
                    discount_amount = round(subtotal * (db_coupon.discount_value / 100.0), 2)
                elif db_coupon.discount_type == "fixed":
                    discount_amount = min(subtotal, db_coupon.discount_value)
        elif coupon_code in PROMO_CODES:
            promo = PROMO_CODES[coupon_code]
            applied_coupon = {"code": coupon_code, "description": promo["description"]}
            if promo["type"] == "percent":
                discount_amount = round(subtotal * (promo["value"] / 100.0), 2)
            elif promo["type"] == "fixed":
                discount_amount = min(subtotal, promo["value"])

    grand_total = max(0.0, round(subtotal - discount_amount, 2))

    return JSONResponse({
        "success": True,
        "items": validated_items,
        "subtotal": round(subtotal, 2),
        "discount": round(discount_amount, 2),
        "applied_coupon": applied_coupon,
        "grand_total": round(grand_total, 2),
        "total_items": sum(i["qty"] for i in validated_items)
    })
