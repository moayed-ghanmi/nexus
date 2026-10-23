from fastapi import APIRouter, Request, Depends, HTTPException, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import desc, asc, or_
from pathlib import Path
from app.database import get_db
from app.models import (
    Category, Product, DigitalKey, KeyStatus, Order, OrderStatus,
    SiteSetting, ProductReview, OrderItem
)
from app.security import get_current_user_from_request

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))

def get_store_settings(db: Session) -> dict:
    records = db.query(SiteSetting).all()
    return {s.key: s.value for s in records}

def check_maintenance(request: Request, db: Session):
    user = get_current_user_from_request(request, db)
    if user and user.is_admin_user:
        return None  # Authenticated admins bypass maintenance mode
    m_record = db.query(SiteSetting).filter(SiteSetting.key == "maintenance_mode").first()
    if m_record and m_record.value.lower() in ["true", "1", "on"]:
        return templates.TemplateResponse(
            request=request,
            name="errors/maintenance.html",
            context={"current_user": user},
            status_code=503
        )
    return None

@router.get("/", response_class=HTMLResponse)
async def store_index(
    request: Request, 
    cat: str = None, 
    platform: str = None,
    region: str = None,
    sort: str = "popular",
    q: str = None, 
    db: Session = Depends(get_db)
):
    maintenance = check_maintenance(request, db)
    if maintenance:
        return maintenance

    settings = get_store_settings(db)
    announcement_text = settings.get("announcement_banner_text", "") if settings.get("announcement_banner_active", "false").lower() in ["true", "1", "on"] else None

    categories = db.query(Category).order_by(Category.display_order.asc()).all()
    query = db.query(Product).filter(Product.is_active == True)

    # 1. Category Filter
    if cat:
        query = query.join(Category).filter(Category.slug == cat)
    
    # 2. Platform Filter
    if platform and platform != "all":
        query = query.filter(Product.platform.ilike(f"%{platform}%"))
        
    # 3. Region Filter
    if region and region != "all":
        query = query.filter(Product.region == region)

    # 4. Search Filter
    if q:
        query = query.filter(Product.title.ilike(f"%{q}%"))

    # 5. Sorting
    if sort == "price_low":
        query = query.order_by(asc(Product.price))
    elif sort == "price_high":
        query = query.order_by(desc(Product.price))
    elif sort == "newest":
        query = query.order_by(desc(Product.id))
    else:  # default "popular"
        query = query.order_by(asc(Product.id))

    products = query.all()
    current_user = get_current_user_from_request(request, db)

    # Distinct platforms and regions for filter bar
    all_platforms = ["Steam", "PlayStation", "Xbox", "PC", "All Devices"]
    all_regions = ["GLOBAL", "US", "EU"]

    return templates.TemplateResponse(
        request=request,
        name="store/index.html",
        context={
            "categories": categories,
            "products": products,
            "current_cat": cat,
            "current_platform": platform or "all",
            "current_region": region or "all",
            "current_sort": sort,
            "search_query": q,
            "all_platforms": all_platforms,
            "all_regions": all_regions,
            "current_user": current_user,
            "announcement_banner_text": announcement_text
        }
    )

@router.get("/api/search")
async def live_search_api(q: str = "", limit: int = 6, db: Session = Depends(get_db)):
    """
    Real-time autocomplete API for storefront navbar and catalog search.
    Performs case-insensitive matching against product title, platform, category, and description.
    Only returns active products with live key availability count.
    """
    clean_q = (q or "").strip()
    if not clean_q:
        return JSONResponse({"query": "", "results": [], "total": 0})

    search_term = f"%{clean_q}%"
    products = db.query(Product).join(Category, isouter=True).filter(
        Product.is_active == True,
        or_(
            Product.title.ilike(search_term),
            Product.platform.ilike(search_term),
            Product.description.ilike(search_term),
            Category.name.ilike(search_term)
        )
    ).limit(limit).all()

    results = []
    for p in products:
        results.append({
            "id": p.id,
            "title": p.title,
            "slug": p.slug,
            "price": p.price,
            "platform": p.platform,
            "region": p.region,
            "badge": p.badge,
            "image_url": p.image_url,
            "category_name": p.category.name if p.category else "Digital",
            "available_keys": p.available_keys_count,
            "url": f"/product/{p.slug}"
        })

    return JSONResponse({
        "query": clean_q,
        "results": results,
        "total": len(results)
    })

@router.get("/product/{slug}", response_class=HTMLResponse)
async def product_detail(request: Request, slug: str, db: Session = Depends(get_db)):
    """Render full digital product detail page with verified reviews & star ratings."""
    maintenance = check_maintenance(request, db)
    if maintenance:
        return maintenance

    product = db.query(Product).filter(Product.slug == slug, Product.is_active == True).first()
    if not product:
        raise HTTPException(status_code=404, detail="Digital product not found.")
    
    current_user = get_current_user_from_request(request, db)
    settings = get_store_settings(db)
    announcement_text = settings.get("announcement_banner_text", "") if settings.get("announcement_banner_active", "false").lower() in ["true", "1", "on"] else None

    # Check if current user has ordered this product
    has_purchased = False
    if current_user:
        has_purchased = db.query(OrderItem).join(Order).filter(
            Order.user_id == current_user.id,
            OrderItem.product_id == product.id,
            Order.status == OrderStatus.COMPLETED.value
        ).first() is not None

    # Approved customer reviews
    reviews = [r for r in product.reviews if r.is_approved]
    
    # Related products from same category
    related_products = db.query(Product).filter(
        Product.category_id == product.category_id,
        Product.id != product.id,
        Product.is_active == True
    ).limit(3).all()

    return templates.TemplateResponse(
        request=request,
        name="store/product.html",
        context={
            "product": product,
            "related_products": related_products,
            "current_user": current_user,
            "reviews": reviews,
            "has_purchased": has_purchased,
            "announcement_banner_text": announcement_text
        }
    )

@router.post("/product/{slug}/review")
async def submit_product_review(
    slug: str,
    request: Request,
    headline: str = Form(...),
    comment: str = Form(...),
    rating: int = Form(5),
    db: Session = Depends(get_db)
):
    current_user = get_current_user_from_request(request, db)
    if not current_user:
        return RedirectResponse(url=f"/auth/login?next=/product/{slug}", status_code=303)

    product = db.query(Product).filter(Product.slug == slug, Product.is_active == True).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    is_verified = db.query(OrderItem).join(Order).filter(
        Order.user_id == current_user.id,
        OrderItem.product_id == product.id,
        Order.status == OrderStatus.COMPLETED.value
    ).first() is not None

    clean_rating = max(1, min(5, int(rating)))
    new_review = ProductReview(
        product_id=product.id,
        user_id=current_user.id,
        rating=clean_rating,
        headline=headline.strip(),
        comment=comment.strip(),
        is_verified_purchase=is_verified,
        is_approved=True
    )
    db.add(new_review)
    # Award 50 XP for writing a product review
    current_user.xp_points = (current_user.xp_points or 0) + 50
    db.commit()

    return RedirectResponse(url=f"/product/{slug}?review=success#reviews", status_code=303)

@router.get("/profile", response_class=HTMLResponse)
async def user_profile(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_from_request(request, db)
    if not user:
        return RedirectResponse(url="/auth/login?next=/profile", status_code=303)
    
    # Query user orders ordered by newest
    user_orders = db.query(Order).filter(Order.user_id == user.id).order_by(Order.id.desc()).all()
    completed_order_ids = [o.id for o in user_orders if o.status == OrderStatus.COMPLETED.value]

    user_keys = []
    if completed_order_ids:
        user_keys = db.query(DigitalKey).filter(
            DigitalKey.order_id.in_(completed_order_ids),
            DigitalKey.status == KeyStatus.SOLD.value
        ).all()

    return templates.TemplateResponse(
        request=request,
        name="auth/profile.html",
        context={
            "current_user": user,
            "orders": user_orders,
            "user_keys": user_keys
        }
    )
