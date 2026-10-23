from datetime import datetime, timezone
import enum
from sqlalchemy import Column, Integer, String, Float, Boolean, Text, DateTime, ForeignKey, Enum
from sqlalchemy.orm import relationship
from app.database import Base

class UserRole(str, enum.Enum):
    CUSTOMER = "customer"
    ADMIN = "admin"
    SUPER_ADMIN = "super_admin"

class KeyStatus(str, enum.Enum):
    AVAILABLE = "available"
    RESERVED = "reserved"
    SOLD = "sold"

class OrderStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    full_name = Column(String(100), nullable=False)
    role = Column(String(50), default=UserRole.CUSTOMER.value, nullable=False)
    is_active = Column(Boolean, default=True)
    wallet_balance = Column(Float, default=0.0, nullable=False)
    xp_points = Column(Integer, default=0, nullable=False)
    referral_code = Column(String(50), unique=True, index=True, nullable=True)
    referred_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    totp_secret = Column(String(64), nullable=True)
    is_totp_enabled = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    last_login = Column(DateTime, nullable=True)

    # Relationships
    orders = relationship("Order", back_populates="user", cascade="all, delete-orphan")
    audit_logs = relationship("AdminAuditLog", back_populates="admin")
    reviews = relationship("ProductReview", back_populates="user", cascade="all, delete-orphan")
    tickets = relationship("Ticket", back_populates="user", cascade="all, delete-orphan", foreign_keys="[Ticket.user_id]")
    wallet_transactions = relationship("WalletTransaction", back_populates="user", cascade="all, delete-orphan", order_by="WalletTransaction.id.desc()")
    redeemed_vouchers = relationship("GiftVoucher", back_populates="redeemed_by", foreign_keys="[GiftVoucher.redeemed_by_user_id]")
    referrals = relationship("User", foreign_keys=[referred_by_id])
    wishlist_items = relationship("WishlistItem", back_populates="user", cascade="all, delete-orphan", order_by="WishlistItem.id.desc()")

    @property
    def is_admin_user(self) -> bool:
        return self.role in [UserRole.ADMIN.value, UserRole.SUPER_ADMIN.value]

    @property
    def loyalty_tier(self) -> dict:
        from app.services.loyalty_service import calculate_tier
        return calculate_tier(self.xp_points or 0)

class Category(Base):
    __tablename__ = "categories"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, nullable=False)
    slug = Column(String(100), unique=True, nullable=False)
    icon = Column(String(50), default="gamepad-2")
    description = Column(String(255), nullable=True)
    display_order = Column(Integer, default=0)

    products = relationship("Product", back_populates="category")

class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(200), nullable=False)
    slug = Column(String(200), unique=True, nullable=False)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=False)
    description = Column(Text, nullable=True)
    platform = Column(String(50), default="Global")  # Steam, PSN, Xbox, PC, etc.
    region = Column(String(50), default="GLOBAL")    # GLOBAL, US, EU, etc.
    price = Column(Float, nullable=False)            # Retail customer price
    cost_price = Column(Float, default=0.0)          # Wholesale cost (used for profit calculation)
    image_url = Column(String(500), nullable=True)
    badge = Column(String(50), nullable=True)        # "HOT", "BESTSELLER", "INSTANT"
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    category = relationship("Category", back_populates="products")
    digital_keys = relationship("DigitalKey", back_populates="product", cascade="all, delete-orphan")
    order_items = relationship("OrderItem", back_populates="product")
    reviews = relationship("ProductReview", back_populates="product", cascade="all, delete-orphan")
    wishlist_entries = relationship("WishlistItem", back_populates="product", cascade="all, delete-orphan")

    @property
    def available_keys_count(self) -> int:
        return sum(1 for k in self.digital_keys if k.status == KeyStatus.AVAILABLE.value)

    @property
    def estimated_profit_per_unit(self) -> float:
        return round(self.price - self.cost_price, 2)

    @property
    def average_rating(self) -> float:
        approved = [r for r in self.reviews if r.is_approved]
        if not approved:
            return 5.0
        return round(sum(r.rating for r in approved) / len(approved), 1)

    @property
    def reviews_count(self) -> int:
        return sum(1 for r in self.reviews if r.is_approved)

    @property
    def wishlist_count(self) -> int:
        return len(self.wishlist_entries)

class DigitalKey(Base):
    __tablename__ = "digital_keys"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    key_code = Column(String(255), nullable=False)  # License key, activation code, or gift serial
    status = Column(String(50), default=KeyStatus.AVAILABLE.value, nullable=False)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    redeemed_at = Column(DateTime, nullable=True)

    product = relationship("Product", back_populates="digital_keys")
    order = relationship("Order", back_populates="assigned_keys")

class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True)
    order_number = Column(String(50), unique=True, index=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    total_amount = Column(Float, nullable=False)
    total_cost = Column(Float, default=0.0)
    profit = Column(Float, default=0.0)
    status = Column(String(50), default=OrderStatus.PENDING.value, nullable=False)
    payment_method = Column(String(50), default="credit_card")
    receipt_file_url = Column(String(500), nullable=True)  # Proof of payment upload path (e.g. image or PDF)
    client_notes = Column(Text, nullable=True)            # Client transaction reference or notes
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    user = relationship("User", back_populates="orders")
    items = relationship("OrderItem", back_populates="order", cascade="all, delete-orphan")
    assigned_keys = relationship("DigitalKey", back_populates="order")
    tickets = relationship("Ticket", back_populates="order")

class OrderItem(Base):
    __tablename__ = "order_items"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    unit_price = Column(Float, nullable=False)
    cost_price = Column(Float, default=0.0)
    quantity = Column(Integer, default=1)
    delivered_keys_json = Column(Text, nullable=True)  # JSON array of keys delivered

    order = relationship("Order", back_populates="items")
    product = relationship("Product", back_populates="order_items")

class PaymentGateway(Base):
    __tablename__ = "payment_gateways"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    code = Column(String(50), unique=True, nullable=False)  # stripe, paypal, crypto, bank_transfer
    is_enabled = Column(Boolean, default=True)
    fee_percentage = Column(Float, default=0.0)
    instructions = Column(Text, nullable=True)
    config_json = Column(Text, nullable=True)  # API keys, wallet addresses, secret keys
    display_order = Column(Integer, default=0)

class AdminAuditLog(Base):
    __tablename__ = "admin_audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    admin_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    action = Column(String(100), nullable=False)
    target = Column(String(100), nullable=True)
    details = Column(Text, nullable=True)
    ip_address = Column(String(50), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    admin = relationship("User", back_populates="audit_logs")

class SiteSetting(Base):
    __tablename__ = "site_settings"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(100), unique=True, nullable=False)
    value = Column(Text, nullable=False)
    description = Column(String(255), nullable=True)

class Coupon(Base):
    __tablename__ = "coupons"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(50), unique=True, index=True, nullable=False)
    discount_type = Column(String(20), default="percent", nullable=False)  # "percent" or "fixed"
    discount_value = Column(Float, nullable=False)
    min_spend = Column(Float, default=0.0)
    max_uses = Column(Integer, default=500)
    times_used = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)
    description = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

class ProductReview(Base):
    __tablename__ = "product_reviews"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    rating = Column(Integer, default=5, nullable=False)  # 1 to 5
    headline = Column(String(150), nullable=False)
    comment = Column(Text, nullable=False)
    is_verified_purchase = Column(Boolean, default=True)
    is_approved = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    product = relationship("Product", back_populates="reviews")
    user = relationship("User", back_populates="reviews")

class TicketStatus(str, enum.Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    WAITING_CLIENT = "waiting_client"
    RESOLVED = "resolved"
    CLOSED = "closed"

class TicketPriority(str, enum.Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"

class Ticket(Base):
    __tablename__ = "tickets"

    id = Column(Integer, primary_key=True, index=True)
    ticket_number = Column(String(50), unique=True, index=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=True)
    subject = Column(String(200), nullable=False)
    category = Column(String(50), default="general", nullable=False)  # "order_issue", "key_redemption", "payment_verification", "billing", "general"
    priority = Column(String(20), default=TicketPriority.NORMAL.value, nullable=False)
    status = Column(String(20), default=TicketStatus.OPEN.value, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    user = relationship("User", back_populates="tickets", foreign_keys=[user_id])
    order = relationship("Order", back_populates="tickets")
    messages = relationship("TicketMessage", back_populates="ticket", cascade="all, delete-orphan", order_by="TicketMessage.id.asc()")

class TicketMessage(Base):
    __tablename__ = "ticket_messages"

    id = Column(Integer, primary_key=True, index=True)
    ticket_id = Column(Integer, ForeignKey("tickets.id"), nullable=False)
    sender_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    sender_role = Column(String(20), default="customer", nullable=False)  # "customer", "admin", "super_admin"
    message = Column(Text, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    ticket = relationship("Ticket", back_populates="messages")
    sender = relationship("User")

class WalletTransaction(Base):
    __tablename__ = "wallet_transactions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    transaction_type = Column(String(50), nullable=False)  # "topup", "purchase", "refund", "adjustment", "voucher", "referral"
    amount = Column(Float, nullable=False)                 # Positive for credit, negative for debit
    balance_after = Column(Float, nullable=False)          # Resulting snapshot balance
    reference = Column(String(100), nullable=True)         # Order #, Slip Ref, Voucher Code, etc.
    description = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    user = relationship("User", back_populates="wallet_transactions")

class GiftVoucher(Base):
    __tablename__ = "gift_vouchers"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(50), unique=True, index=True, nullable=False)  # e.g. "NXUS-GIFT-9921-7788"
    amount = Column(Float, nullable=False)                              # Dollar balance to credit
    is_redeemed = Column(Boolean, default=False, nullable=False)
    redeemed_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    redeemed_at = Column(DateTime, nullable=True)
    batch_name = Column(String(100), nullable=True)                     # e.g. "VIP Launch Batch", "Summer Promo"
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    expires_at = Column(DateTime, nullable=True)

    redeemed_by = relationship("User", foreign_keys=[redeemed_by_user_id], back_populates="redeemed_vouchers")

class WishlistItem(Base):
    __tablename__ = "wishlist_items"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    notify_on_restock = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    user = relationship("User", back_populates="wishlist_items")
    product = relationship("Product", back_populates="wishlist_entries")
