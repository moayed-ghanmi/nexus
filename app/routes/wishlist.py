from pathlib import Path
from fastapi import APIRouter, Request, Depends, HTTPException, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User, Product, WishlistItem
from app.security import get_current_user_from_request

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))

@router.get("/wishlist", response_class=HTMLResponse)
async def customer_wishlist_page(request: Request, db: Session = Depends(get_db)):
    """Render customer's personalized wishlist dashboard with live stock indicators and 1-click cart/wallet actions."""
    current_user = get_current_user_from_request(request, db)
    if not current_user:
        return RedirectResponse(url="/auth/login?next=/wishlist", status_code=303)

    items = db.query(WishlistItem).filter(
        WishlistItem.user_id == current_user.id
    ).order_by(WishlistItem.id.desc()).all()

    total_items = len(items)
    in_stock_count = sum(1 for item in items if item.product and item.product.available_keys_count > 0)
    total_value = sum(item.product.price for item in items if item.product)

    return templates.TemplateResponse(
        request=request,
        name="store/wishlist.html",
        context={
            "current_user": current_user,
            "items": items,
            "total_items": total_items,
            "in_stock_count": in_stock_count,
            "total_value": round(total_value, 2)
        }
    )

@router.post("/api/wishlist/toggle")
async def toggle_wishlist_item(request: Request, db: Session = Depends(get_db)):
    """AJAX endpoint to add or remove a product from customer's wishlist."""
    current_user = get_current_user_from_request(request, db)
    if not current_user:
        return JSONResponse({
            "success": False,
            "error": "auth_required",
            "message": "Please sign in to save products to your wishlist."
        }, status_code=401)

    try:
        payload = await request.json()
        product_id = int(payload.get("product_id", 0))
    except Exception:
        product_id = 0

    product = db.query(Product).filter(Product.id == product_id, Product.is_active == True).first()
    if not product:
        return JSONResponse({"success": False, "error": "Product not found"}, status_code=404)

    existing = db.query(WishlistItem).filter(
        WishlistItem.user_id == current_user.id,
        WishlistItem.product_id == product.id
    ).first()

    if existing:
        db.delete(existing)
        db.commit()
        in_wishlist = False
        message = f"Removed '{product.title}' from your wishlist."
    else:
        new_item = WishlistItem(
            user_id=current_user.id,
            product_id=product.id,
            notify_on_restock=True
        )
        db.add(new_item)
        db.commit()
        in_wishlist = True
        message = f"Saved '{product.title}' to your wishlist!"

    total_count = db.query(WishlistItem).filter(WishlistItem.user_id == current_user.id).count()

    return JSONResponse({
        "success": True,
        "in_wishlist": in_wishlist,
        "product_id": product.id,
        "count": total_count,
        "message": message
    })

@router.get("/api/wishlist/ids")
async def get_wishlist_product_ids(request: Request, db: Session = Depends(get_db)):
    """Returns list of product IDs bookmarked in the active user's wishlist."""
    current_user = get_current_user_from_request(request, db)
    if not current_user:
        return JSONResponse({"ids": []})

    items = db.query(WishlistItem.product_id).filter(WishlistItem.user_id == current_user.id).all()
    ids = [i[0] for i in items]
    return JSONResponse({"ids": ids, "count": len(ids)})

@router.post("/wishlist/clear")
async def clear_wishlist(request: Request, db: Session = Depends(get_db)):
    """Clears all items from user's wishlist."""
    current_user = get_current_user_from_request(request, db)
    if current_user:
        db.query(WishlistItem).filter(WishlistItem.user_id == current_user.id).delete()
        db.commit()
    return RedirectResponse(url="/wishlist", status_code=303)
