from sqlalchemy.orm import Session
from app.models import User, Order, OrderStatus

# Loyalty Tier Definitions
TIERS = [
    {
        "name": "Cyber VIP",
        "slug": "cyber_vip",
        "min_xp": 3000,
        "discount_percent": 10,
        "badge_color": "text-purple-400 bg-purple-950/80 border-purple-500/40",
        "border_color": "border-purple-500",
        "glow_color": "shadow-purple-500/30",
        "icon": "zap",
        "perks": "10% store-wide discount, VIP neon glow badge, top-priority dispatcher"
    },
    {
        "name": "Gold Operative",
        "slug": "gold",
        "min_xp": 1500,
        "discount_percent": 5,
        "badge_color": "text-amber-400 bg-amber-950/80 border-amber-500/40",
        "border_color": "border-amber-500",
        "glow_color": "shadow-amber-500/30",
        "icon": "award",
        "perks": "5% store-wide discount, Gold badge in reviews, priority key queue"
    },
    {
        "name": "Silver Elite",
        "slug": "silver",
        "min_xp": 500,
        "discount_percent": 2,
        "badge_color": "text-slate-300 bg-slate-800/80 border-slate-600/40",
        "border_color": "border-slate-500",
        "glow_color": "shadow-slate-500/20",
        "icon": "shield",
        "perks": "2% store-wide discount, Silver customer status, early promo alerts"
    },
    {
        "name": "Bronze Member",
        "slug": "bronze",
        "min_xp": 0,
        "discount_percent": 0,
        "badge_color": "text-amber-600 bg-amber-950/30 border-amber-800/30",
        "border_color": "border-amber-800",
        "glow_color": "shadow-amber-900/10",
        "icon": "user",
        "perks": "Base membership, digital key vault, 24/7 ticket support"
    }
]

def calculate_tier(xp: int) -> dict:
    """Calculates tier details, discount percentage, and progress towards the next tier."""
    xp = max(0, xp or 0)
    for i, tier in enumerate(TIERS):
        if xp >= tier["min_xp"]:
            if i == 0:
                # Top tier (Cyber VIP)
                next_tier = None
                next_xp = 3000
                progress = 100
                xp_needed = 0
            else:
                next_tier = TIERS[i - 1]["name"]
                next_xp = TIERS[i - 1]["min_xp"]
                current_base = tier["min_xp"]
                progress = min(100, int(((xp - current_base) / (next_xp - current_base)) * 100))
                xp_needed = next_xp - xp

            result = dict(tier)
            result.update({
                "next_tier": next_tier,
                "next_xp": next_xp,
                "progress_percent": progress,
                "xp_needed": xp_needed
            })
            return result
    return dict(TIERS[-1])

def award_order_xp(order: Order, db: Session) -> int:
    """
    Awards loyalty XP to the buyer when an order is completed.
    Rate: 10 XP per $1 spent (minimum 10 XP).
    """
    if not order or not order.user_id:
        return 0

    user = order.user or db.query(User).filter(User.id == order.user_id).first()
    if not user:
        return 0

    earned_xp = max(10, int(order.total_amount * 10))
    user.xp_points = (user.xp_points or 0) + earned_xp
    db.commit()
    db.refresh(user)
    return earned_xp

def award_review_xp(user: User, db: Session) -> int:
    """Awards 50 XP to the user for submitting a verified product review."""
    if not user:
        return 0
    earned = 50
    user.xp_points = (user.xp_points or 0) + earned
    db.commit()
    db.refresh(user)
    return earned

def award_referral_xp(user: User, db: Session) -> int:
    """Awards 100 XP to the user when a referred friend signs up or purchases."""
    if not user:
        return 0
    earned = 100
    user.xp_points = (user.xp_points or 0) + earned
    db.commit()
    db.refresh(user)
    return earned

def get_loyalty_discount(user: User, subtotal: float) -> tuple[float, str, int]:
    """
    Returns (discount_amount, tier_name, discount_percent) for a user on a given subtotal.
    """
    if not user:
        return 0.0, "Guest", 0
    tier = calculate_tier(user.xp_points)
    pct = tier.get("discount_percent", 0)
    if pct <= 0:
        return 0.0, tier["name"], 0
    discount_amount = round(subtotal * (pct / 100.0), 2)
    return discount_amount, tier["name"], pct

def admin_adjust_xp(user: User, delta_xp: int, db: Session) -> int:
    """Administratively increases or decreases a customer's XP."""
    if not user:
        return 0
    current = user.xp_points or 0
    new_xp = max(0, current + delta_xp)
    user.xp_points = new_xp
    db.commit()
    db.refresh(user)
    return new_xp
