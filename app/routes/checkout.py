import json
import time
import random
from datetime import datetime, timezone
from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from pathlib import Path
from app.database import get_db
from app.models import (
    User, Product, DigitalKey, KeyStatus, Order, 
    OrderItem, OrderStatus, PaymentGateway, Coupon
)
from app.security import get_current_user_from_request
from app.routes.cart import PROMO_CODES
from app.services.email_service import send_order_receipt_email
from app.services.invoice_service import generate_order_invoice_pdf
from app.services import wallet_service, loyalty_service

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))

@router.get("/checkout", response_class=HTMLResponse)
async def checkout_page(request: Request, db: Session = Depends(get_db)):
    """Render the secure digital checkout page."""
    current_user = get_current_user_from_request(request, db)
    if not current_user:
        return RedirectResponse(url="/auth/login?next=/checkout", status_code=303)

    gateways = db.query(PaymentGateway).filter(
        PaymentGateway.is_enabled == True
    ).order_by(PaymentGateway.display_order.asc()).all()

    return templates.TemplateResponse(
        request=request,
        name="store/checkout.html",
        context={
            "current_user": current_user,
            "gateways": gateways
        }
    )

@router.post("/checkout/process")
async def process_checkout(request: Request, db: Session = Depends(get_db)):
    """
    Atomically processes order checkout, deducts and claims digital keys from inventory,
    calculates net financial profit, handles client receipt upload, and triggers email confirmation.
    """
    current_user = get_current_user_from_request(request, db)
    if not current_user:
        return JSONResponse({"success": False, "error": "Authentication required. Please sign in."}, status_code=401)

    content_type = request.headers.get("content-type", "").lower()
    receipt_file = None
    client_notes = ""
    items_in = []
    gateway_code = "wallet"
    coupon_code = ""

    if "multipart/form-data" in content_type:
        form = await request.form()
        items_raw = form.get("items") or form.get("items_json") or "[]"
        try:
            items_in = json.loads(items_raw) if isinstance(items_raw, str) else items_raw
        except Exception:
            items_in = []
        gateway_code = form.get("payment_method", "wallet")
        coupon_code = str(form.get("coupon", "")).strip().upper()
        client_notes = str(form.get("client_notes", "")).strip()
        receipt_file = form.get("receipt_file")
    else:
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        items_in = payload.get("items") or payload.get("items_json") or []
        gateway_code = payload.get("payment_method", "wallet")
        coupon_code = payload.get("coupon", "").strip().upper()
        client_notes = payload.get("client_notes", "").strip()

    if not items_in:
        return JSONResponse({"success": False, "error": "Your cart is empty."}, status_code=400)

    # Verify payment gateway
    gateway = db.query(PaymentGateway).filter(
        PaymentGateway.code == gateway_code, 
        PaymentGateway.is_enabled == True
    ).first()
    if not gateway:
        gateway = db.query(PaymentGateway).filter(PaymentGateway.is_enabled == True).order_by(PaymentGateway.display_order.asc()).first()

    # Step 1: Stock Validation & Key Lock
    order_items_to_create = []
    keys_to_claim = []
    raw_subtotal = 0.0
    total_wholesale_cost = 0.0

    for item in items_in:
        p_id = item.get("id") or item.get("product_id")
        qty = max(1, int(item.get("qty") or item.get("quantity") or 1))
        
        product = db.query(Product).filter(Product.id == p_id, Product.is_active == True).first()
        if not product:
            return JSONResponse({"success": False, "error": f"Product ID {p_id} not found."}, status_code=404)

        # Check available keys in inventory
        available_keys = db.query(DigitalKey).filter(
            DigitalKey.product_id == product.id,
            DigitalKey.status == KeyStatus.AVAILABLE.value
        ).limit(qty).all()

        if len(available_keys) < qty:
            return JSONResponse({
                "success": False, 
                "error": f"Insufficient stock for '{product.title}'. Only {len(available_keys)} keys currently available."
            }, status_code=400)

        item_total = round(product.price * qty, 2)
        item_cost = round(product.cost_price * qty, 2)

        raw_subtotal += item_total
        total_wholesale_cost += item_cost

        # Collect keys to claim
        product_claimed_keys = []
        for k in available_keys:
            product_claimed_keys.append(k.key_code)
            keys_to_claim.append(k)

        order_items_to_create.append({
            "product": product,
            "unit_price": product.price,
            "cost_price": product.cost_price,
            "quantity": qty,
            "keys": product_claimed_keys
        })

    # Step 2: Apply Coupon Discount & Customer VIP Loyalty Discount
    discount_amount = 0.0
    if coupon_code:
        db_coupon = db.query(Coupon).filter(
            Coupon.code == coupon_code,
            Coupon.is_active == True
        ).first()

        if db_coupon:
            if raw_subtotal >= db_coupon.min_spend and db_coupon.times_used < db_coupon.max_uses:
                if db_coupon.discount_type == "percent":
                    discount_amount = round(raw_subtotal * (db_coupon.discount_value / 100.0), 2)
                elif db_coupon.discount_type == "fixed":
                    discount_amount = min(raw_subtotal, db_coupon.discount_value)
                db_coupon.times_used += 1
        elif coupon_code in PROMO_CODES:
            promo = PROMO_CODES[coupon_code]
            if promo["type"] == "percent":
                discount_amount = round(raw_subtotal * (promo["value"] / 100.0), 2)
            elif promo["type"] == "fixed":
                discount_amount = min(raw_subtotal, promo["value"])

    # VIP Loyalty Tier Discount (2%, 5%, or 10% based on customer XP rank)
    loyalty_discount, tier_name, tier_pct = loyalty_service.get_loyalty_discount(current_user, raw_subtotal)
    total_savings = discount_amount + loyalty_discount

    discounted_subtotal = max(0.0, raw_subtotal - total_savings)

    # Step 3: Gateway Processing Fee & Wallet Validation
    is_wallet_payment = (gateway_code == "wallet" or (gateway and gateway.code == "wallet"))
    if is_wallet_payment:
        gateway_fee = 0.0
        grand_total = discounted_subtotal
        current_bal = round(float(current_user.wallet_balance or 0.0), 2)
        if current_bal < grand_total:
            return JSONResponse({
                "success": False,
                "error": f"Insufficient wallet store credit. Available: ${current_bal:.2f}, Required: ${grand_total:.2f}. Please top up your wallet to proceed."
            }, status_code=400)
    else:
        gateway_fee = round(discounted_subtotal * (gateway.fee_percentage / 100.0), 2)
        grand_total = round(discounted_subtotal + gateway_fee, 2)

    # Step 4: Profit Calculation
    net_profit = round(grand_total - total_wholesale_cost, 2)

    # Step 5: Atomically create Order, Debit Wallet (if applicable) & Claim Keys
    order_number = f"ORD-{int(time.time())}-{random.randint(100, 999)}"
    
    # Direct bank wire / D17 manual transfer begins in PENDING status awaiting admin verification
    is_pending = (gateway.code in ["bank_transfer", "d17"]) and not is_wallet_payment
    initial_status = OrderStatus.PENDING.value if is_pending else OrderStatus.COMPLETED.value
    initial_profit = 0.0 if is_pending else net_profit

    # Atomically debit customer wallet balance if using store credit
    if is_wallet_payment:
        wallet_service.debit_wallet(
            user=current_user,
            amount=grand_total,
            reference=order_number,
            description=f"1-Click Purchase for Order {order_number}",
            db=db
        )

    # Handle client receipt file upload if provided
    receipt_url = None
    if receipt_file and hasattr(receipt_file, "filename") and receipt_file.filename:
        orig_name = receipt_file.filename
        ext = orig_name.rsplit(".", 1)[-1].lower() if "." in orig_name else "jpg"
        if ext in ["jpg", "jpeg", "png", "webp", "pdf"]:
            upload_dir = Path(__file__).resolve().parent.parent.parent / "static" / "uploads" / "receipts"
            upload_dir.mkdir(parents=True, exist_ok=True)
            safe_filename = f"receipt_{order_number}_{int(time.time())}.{ext}"
            file_path = upload_dir / safe_filename
            content = await receipt_file.read()
            file_path.write_bytes(content)
            receipt_url = f"/static/uploads/receipts/{safe_filename}"

    order = Order(
        order_number=order_number,
        user_id=current_user.id,
        total_amount=grand_total,
        total_cost=total_wholesale_cost,
        profit=initial_profit,
        status=initial_status,
        payment_method="Nexus Wallet Balance" if is_wallet_payment else gateway.name,
        receipt_file_url=receipt_url,
        client_notes=client_notes if client_notes else None,
        created_at=datetime.now(timezone.utc)
    )
    db.add(order)
    db.commit()
    db.refresh(order)

    # Reserve or finalize keys
    now_utc = datetime.now(timezone.utc)
    for k in keys_to_claim:
        k.status = KeyStatus.RESERVED.value if is_pending else KeyStatus.SOLD.value
        k.order_id = order.id
        k.redeemed_at = None if is_pending else now_utc

    # Create OrderItems (if pending, keys are held and not yet revealed to customer)
    for oi in order_items_to_create:
        item_record = OrderItem(
            order_id=order.id,
            product_id=oi["product"].id,
            unit_price=oi["unit_price"],
            cost_price=oi["cost_price"],
            quantity=oi["quantity"],
            delivered_keys_json=json.dumps([]) if is_pending else json.dumps(oi["keys"])
        )
        db.add(item_record)

    db.commit()

    # If completed immediately (e.g. Card, PayPal, Crypto, Wallet), dispatch receipt email, referral reward & loyalty XP
    earned_xp = 0
    if not is_pending:
        try:
            send_order_receipt_email(order, db)
        except Exception as ex:
            print(f"[Checkout] Instant email receipt error: {ex}")
        try:
            wallet_service.award_referral_reward(order, db)
        except Exception as ex:
            print(f"[Checkout] Referral commission error: {ex}")
        try:
            earned_xp = loyalty_service.award_order_xp(order, db)
        except Exception as ex:
            print(f"[Checkout] Loyalty XP award error: {ex}")

    return JSONResponse({
        "success": True,
        "order_number": order.order_number,
        "status": order.status,
        "earned_xp": earned_xp,
        "redirect_url": f"/order/success/{order.order_number}"
    })

@router.post("/order/{order_number}/upload-receipt")
async def upload_order_receipt(
    order_number: str,
    request: Request,
    db: Session = Depends(get_db)
):
    """Allows client to upload or replace proof of payment receipt for an existing order."""
    current_user = get_current_user_from_request(request, db)
    if not current_user:
        return JSONResponse({"success": False, "error": "Authentication required."}, status_code=401)

    order = db.query(Order).filter(Order.order_number == order_number).first()
    if not order or (order.user_id != current_user.id and not current_user.is_admin_user):
        return JSONResponse({"success": False, "error": "Order not found or unauthorized."}, status_code=404)

    form = await request.form()
    receipt_file = form.get("receipt_file")
    client_notes = str(form.get("client_notes", "")).strip()

    if client_notes:
        order.client_notes = client_notes

    if not receipt_file or not hasattr(receipt_file, "filename") or not receipt_file.filename:
        db.commit()
        return JSONResponse({"success": True, "message": "Transaction notes saved successfully."})

    orig_name = receipt_file.filename
    ext = orig_name.rsplit(".", 1)[-1].lower() if "." in orig_name else "jpg"
    if ext not in ["jpg", "jpeg", "png", "webp", "pdf"]:
        return JSONResponse({
            "success": False, 
            "error": "Invalid format. Only JPG, PNG, WEBP, or PDF files are accepted."
        }, status_code=400)

    upload_dir = Path(__file__).resolve().parent.parent.parent / "static" / "uploads" / "receipts"
    upload_dir.mkdir(parents=True, exist_ok=True)
    safe_filename = f"receipt_{order.order_number}_{int(time.time())}.{ext}"
    file_path = upload_dir / safe_filename
    content = await receipt_file.read()
    file_path.write_bytes(content)

    order.receipt_file_url = f"/static/uploads/receipts/{safe_filename}"
    db.commit()

    return JSONResponse({
        "success": True,
        "message": "Payment receipt uploaded successfully! Our team will review and approve your order shortly.",
        "receipt_file_url": order.receipt_file_url
    })

@router.get("/order/success/{order_number}", response_class=HTMLResponse)
async def order_success_page(request: Request, order_number: str, db: Session = Depends(get_db)):
    """Render order confirmation & instant digital key revelation vault."""
    current_user = get_current_user_from_request(request, db)
    if not current_user:
        return RedirectResponse(url="/auth/login", status_code=303)

    order = db.query(Order).filter(Order.order_number == order_number).first()
    if not order or (order.user_id != current_user.id and not current_user.is_admin_user):
        raise HTTPException(status_code=404, detail="Order not found or unauthorized.")

    return templates.TemplateResponse(
        request=request,
        name="store/order_success.html",
        context={
            "current_user": current_user,
            "order": order
        }
    )

@router.get("/order/{order_number}/invoice")
async def download_order_invoice(
    order_number: str,
    request: Request,
    download: bool = False,
    db: Session = Depends(get_db)
):
    """
    Generates and returns the official, cryptographically verified PDF invoice.
    Allows customers to view inline in browser or download as attachment.
    """
    current_user = get_current_user_from_request(request, db)
    if not current_user:
        return RedirectResponse(url=f"/auth/login?next=/order/{order_number}/invoice", status_code=303)

    order = db.query(Order).filter(Order.order_number == order_number).first()
    if not order or (order.user_id != current_user.id and not current_user.is_admin_user):
        raise HTTPException(status_code=404, detail="Order not found or unauthorized.")

    pdf_bytes = generate_order_invoice_pdf(order, db, cache_to_disk=True)
    disposition = "attachment" if download else "inline"
    filename = f"Invoice-{order.order_number}.pdf"

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'{disposition}; filename="{filename}"',
            "Cache-Control": "public, max-age=3600"
        }
    )

