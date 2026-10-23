import time
import random
from pathlib import Path
from datetime import datetime, timezone
from fastapi import APIRouter, Request, Depends, HTTPException, Form, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User, PaymentGateway, WalletTransaction
from app.security import get_current_user_from_request
from app.services import wallet_service

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))

@router.get("/wallet", response_class=HTMLResponse)
async def customer_wallet_page(request: Request, db: Session = Depends(get_db)):
    """Render customer wallet balance dashboard, instant top-up form, and immutable audit ledger."""
    current_user = get_current_user_from_request(request, db)
    if not current_user:
        return RedirectResponse(url="/auth/login?next=/wallet", status_code=303)

    balance = wallet_service.get_wallet_balance(current_user)

    transactions = db.query(WalletTransaction).filter(
        WalletTransaction.user_id == current_user.id
    ).order_by(WalletTransaction.id.desc()).all()

    # Gateways available for funding wallet (excluding wallet itself)
    gateways = db.query(PaymentGateway).filter(
        PaymentGateway.is_enabled == True,
        PaymentGateway.code != "wallet"
    ).order_by(PaymentGateway.display_order.asc()).all()

    # Calculate statistics
    total_credited = sum(t.amount for t in transactions if t.amount > 0)
    total_spent = abs(sum(t.amount for t in transactions if t.amount < 0))
    referral_earnings = sum(t.amount for t in transactions if t.transaction_type == "referral")

    # Ensure referral code
    if not current_user.referral_code:
        import uuid
        current_user.referral_code = f"REF-{current_user.full_name[:3].upper()}{uuid.uuid4().hex[:4].upper()}"
        db.commit()

    referrals_count = db.query(User).filter(User.referred_by_id == current_user.id).count()

    return templates.TemplateResponse(
        request=request,
        name="store/wallet.html",
        context={
            "current_user": current_user,
            "balance": balance,
            "transactions": transactions,
            "gateways": gateways,
            "total_credited": round(total_credited, 2),
            "total_spent": round(total_spent, 2),
            "referral_code": current_user.referral_code,
            "referrals_count": referrals_count,
            "referral_earnings": round(referral_earnings, 2)
        }
    )

@router.post("/wallet/topup")
async def customer_wallet_topup(
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Credits customer wallet balance via selected payment method.
    Supports instant gateways (Stripe, PayPal, Crypto) and manual transfer uploads (D17, R.I.B).
    """
    current_user = get_current_user_from_request(request, db)
    if not current_user:
        return JSONResponse({"success": False, "error": "Authentication required. Please sign in."}, status_code=401)

    content_type = request.headers.get("content-type", "").lower()
    amount = 0.0
    payment_method = "stripe"
    notes = ""
    receipt_file = None

    if "multipart/form-data" in content_type:
        form = await request.form()
        try:
            amount = float(form.get("amount", 0.0))
        except (ValueError, TypeError):
            amount = 0.0
        payment_method = str(form.get("payment_method", "stripe")).strip()
        notes = str(form.get("notes", "")).strip()
        receipt_file = form.get("receipt_file")
    else:
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        try:
            amount = float(payload.get("amount", 0.0))
        except (ValueError, TypeError):
            amount = 0.0
        payment_method = str(payload.get("payment_method", "stripe")).strip()
        notes = str(payload.get("notes", "")).strip()

    if amount < 5.0 or amount > 2500.0:
        error_msg = "Top-up amount must be between $5.00 and $2,500.00."
        if "multipart/form-data" in content_type and not request.headers.get("accept", "").startswith("application/json"):
            return RedirectResponse(url=f"/wallet?error={error_msg}", status_code=303)
        return JSONResponse({"success": False, "error": error_msg}, status_code=400)

    gateway = db.query(PaymentGateway).filter(
        PaymentGateway.code == payment_method,
        PaymentGateway.is_enabled == True
    ).first()
    gateway_name = gateway.name if gateway else payment_method.capitalize()

    # Save optional receipt slip if uploaded
    receipt_url = None
    if receipt_file and hasattr(receipt_file, "filename") and receipt_file.filename:
        orig_name = receipt_file.filename
        ext = orig_name.rsplit(".", 1)[-1].lower() if "." in orig_name else "jpg"
        if ext in ["jpg", "jpeg", "png", "webp", "pdf"]:
            upload_dir = Path(__file__).resolve().parent.parent.parent / "static" / "uploads" / "receipts"
            upload_dir.mkdir(parents=True, exist_ok=True)
            safe_name = f"wallet_topup_{current_user.id}_{int(time.time())}.{ext}"
            file_path = upload_dir / safe_name
            content = await receipt_file.read()
            file_path.write_bytes(content)
            receipt_url = f"/static/uploads/receipts/{safe_name}"

    ref_code = f"TOPUP-{int(time.time())}-{random.randint(100, 999)}"
    desc = f"Store Credit Top-Up via {gateway_name}"
    if notes:
        desc += f" (Ref: {notes[:30]})"

    txn = wallet_service.credit_wallet(
        user=current_user,
        amount=amount,
        transaction_type="topup",
        reference=ref_code,
        description=desc,
        db=db
    )

    if "multipart/form-data" in content_type and not request.headers.get("accept", "").startswith("application/json"):
        return RedirectResponse(url=f"/wallet?success=topup&amount={amount:.2f}", status_code=303)

    return JSONResponse({
        "success": True,
        "message": f"Successfully credited ${amount:.2f} to your Nexus Wallet!",
        "new_balance": current_user.wallet_balance,
        "reference": ref_code,
        "transaction_id": txn.id
    })

@router.post("/wallet/redeem-voucher")
async def customer_redeem_voucher(
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Redeems a 16-character promotional gift voucher code and credits the amount to the customer's wallet.
    """
    current_user = get_current_user_from_request(request, db)
    if not current_user:
        return JSONResponse({"success": False, "error": "Authentication required. Please sign in."}, status_code=401)

    content_type = request.headers.get("content-type", "").lower()
    code = ""

    if "multipart/form-data" in content_type or "application/x-www-form-urlencoded" in content_type:
        form = await request.form()
        code = str(form.get("code", "")).strip().upper()
    else:
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        code = str(payload.get("code", "")).strip().upper()

    if not code:
        return JSONResponse({"success": False, "error": "Please enter a gift voucher code."}, status_code=400)

    try:
        voucher, txn = wallet_service.redeem_voucher(current_user, code, db)
        if "multipart/form-data" in content_type and not request.headers.get("accept", "").startswith("application/json"):
            return RedirectResponse(url=f"/wallet?success=voucher&amount={voucher.amount:.2f}", status_code=303)

        return JSONResponse({
            "success": True,
            "message": f"Gift voucher '{voucher.code}' redeemed successfully! +${voucher.amount:.2f} credited to your wallet.",
            "code": voucher.code,
            "amount": voucher.amount,
            "new_balance": current_user.wallet_balance
        })
    except ValueError as ex:
        if "multipart/form-data" in content_type and not request.headers.get("accept", "").startswith("application/json"):
            return RedirectResponse(url=f"/wallet?error={str(ex)}", status_code=303)
        return JSONResponse({"success": False, "error": str(ex)}, status_code=400)

@router.get("/api/wallet/balance")
async def get_wallet_balance_api(request: Request, db: Session = Depends(get_db)):
    """API endpoint for live wallet balance checking."""
    current_user = get_current_user_from_request(request, db)
    if not current_user:
        return JSONResponse({"success": False, "error": "Not authenticated"}, status_code=401)

    return JSONResponse({
        "success": True,
        "balance": wallet_service.get_wallet_balance(current_user),
        "email": current_user.email
    })
