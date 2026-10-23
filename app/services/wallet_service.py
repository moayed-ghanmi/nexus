"""
NEXUS KEYS - Customer Wallet & Store Credit Engine
Manages atomic balance credits, debits, transaction ledgers, 1-click checkout validation,
digital gift voucher redemptions, and affiliate referral reward dispatching.
"""

from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.models import User, WalletTransaction, GiftVoucher

def get_wallet_balance(user: User) -> float:
    return round(float(user.wallet_balance or 0.0), 2)

def credit_wallet(
    user: User,
    amount: float,
    transaction_type: str,
    reference: str,
    description: str,
    db: Session
) -> WalletTransaction:
    """Atomically credits customer's wallet balance and records an immutable audit ledger entry."""
    amount = round(float(amount), 2)
    if amount <= 0:
        raise ValueError("Credit amount must be greater than zero.")

    user.wallet_balance = round(float(user.wallet_balance or 0.0) + amount, 2)

    txn = WalletTransaction(
        user_id=user.id,
        transaction_type=transaction_type,
        amount=amount,
        balance_after=user.wallet_balance,
        reference=reference,
        description=description,
        created_at=datetime.now(timezone.utc)
    )
    db.add(txn)
    db.commit()
    db.refresh(txn)
    return txn

def debit_wallet(
    user: User,
    amount: float,
    reference: str,
    description: str,
    db: Session
) -> WalletTransaction:
    """Atomically debits customer's wallet balance for 1-click purchases. Rejects if funds are insufficient."""
    amount = round(float(amount), 2)
    if amount <= 0:
        raise ValueError("Debit amount must be greater than zero.")

    current_bal = round(float(user.wallet_balance or 0.0), 2)
    if current_bal < amount:
        raise ValueError(f"Insufficient funds: available ${current_bal:.2f}, required ${amount:.2f}")

    user.wallet_balance = round(current_bal - amount, 2)

    txn = WalletTransaction(
        user_id=user.id,
        transaction_type="purchase",
        amount=-amount,
        balance_after=user.wallet_balance,
        reference=reference,
        description=description,
        created_at=datetime.now(timezone.utc)
    )
    db.add(txn)
    db.commit()
    db.refresh(txn)
    return txn

def redeem_voucher(user: User, code: str, db: Session):
    """
    Validates a gift card / voucher code and credits the amount to customer's wallet.
    Raises ValueError on invalid, already-redeemed, or expired codes.
    """
    clean_code = str(code).strip().upper()
    if not clean_code:
        raise ValueError("Voucher code cannot be empty.")

    voucher = db.query(GiftVoucher).filter(GiftVoucher.code == clean_code).first()
    if not voucher:
        raise ValueError("Invalid gift voucher code. Please check and try again.")

    if voucher.is_redeemed:
        redeemed_date = voucher.redeemed_at.strftime("%Y-%m-%d %H:%M") if voucher.redeemed_at else "previously"
        raise ValueError(f"This voucher was already redeemed on {redeemed_date}.")

    now = datetime.now(timezone.utc)
    if voucher.expires_at and voucher.expires_at < now:
        raise ValueError("This gift voucher has expired.")

    # Mark as redeemed
    voucher.is_redeemed = True
    voucher.redeemed_by_user_id = user.id
    voucher.redeemed_at = now

    # Credit wallet atomically
    txn = credit_wallet(
        user=user,
        amount=voucher.amount,
        transaction_type="voucher",
        reference=voucher.code,
        description=f"Redeemed Gift Voucher ({voucher.code})",
        db=db
    )
    return voucher, txn

def award_referral_reward(order, db: Session):
    """
    Awards 5% affiliate store credit to the customer who referred the buyer upon order completion.
    """
    if not order:
        return None

    buyer = order.user if getattr(order, "user", None) else db.query(User).filter(User.id == order.user_id).first()
    if not buyer or not buyer.referred_by_id:
        return None

    referrer = db.query(User).filter(User.id == buyer.referred_by_id).first()
    if not referrer or referrer.id == buyer.id:
        return None

    # Check if reward for this order was already granted
    existing = db.query(WalletTransaction).filter(
        WalletTransaction.user_id == referrer.id,
        WalletTransaction.transaction_type == "referral",
        WalletTransaction.reference == order.order_number
    ).first()
    if existing:
        return existing

    # Calculate 5% commission on order total
    commission = round(float(order.total_amount) * 0.05, 2)
    if commission <= 0:
        return None

    txn = credit_wallet(
        user=referrer,
        amount=commission,
        transaction_type="referral",
        reference=order.order_number,
        description=f"5% Affiliate Reward from {buyer.full_name.split()[0]}'s order ({order.order_number})",
        db=db
    )
    return txn
