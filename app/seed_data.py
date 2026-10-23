from datetime import datetime
from app.database import engine, Base, SessionLocal
from app.models import (
    User, UserRole, Category, Product, DigitalKey, 
    KeyStatus, PaymentGateway, SiteSetting, Order, OrderStatus,
    Coupon, ProductReview
)
from app.security import hash_password

def ensure_auxiliary_seed_data(db):
    """Ensures coupons, site settings, and initial reviews exist even if core users were already seeded."""
    # 1. Coupons
    if db.query(Coupon).count() == 0:
        default_coupons = [
            Coupon(
                code="GAMER10",
                discount_type="percent",
                discount_value=10.0,
                min_spend=15.0,
                max_uses=500,
                times_used=0,
                is_active=True,
                description="10% Gamers Discount on cards and subscriptions"
            ),
            Coupon(
                code="WELCOME5",
                discount_type="fixed",
                discount_value=5.0,
                min_spend=20.0,
                max_uses=500,
                times_used=0,
                is_active=True,
                description="$5 Off Welcome Voucher for new clients"
            ),
            Coupon(
                code="NEXUS2026",
                discount_type="percent",
                discount_value=15.0,
                min_spend=30.0,
                max_uses=200,
                times_used=0,
                is_active=True,
                description="15% VIP Launch Promotion"
            )
        ]
        db.add_all(default_coupons)
        db.commit()

    # 2. Site Settings
    settings_to_ensure = {
        "store_name": "NEXUS KEYS",
        "tagline": "Instant 24/7 Digital Keys, Gaming Gift Cards & Software Subscriptions",
        "currency_symbol": "$",
        "support_email": "support@nexuskeys.store",
        "announcement_banner_text": "🔥 FLASH SALE: Use coupon code GAMER10 at checkout for 10% OFF all digital gift cards!",
        "announcement_banner_active": "true",
        "maintenance_mode": "false",
        "admin_secret_path": "/portal-vault-admin",
        "smtp_host": "smtp.gmail.com",
        "smtp_port": "587",
        "smtp_user": "",
        "smtp_password": "",
        "smtp_from_email": "orders@nexuskeys.store",
        "smtp_use_tls": "true"
    }
    for key, val in settings_to_ensure.items():
        existing = db.query(SiteSetting).filter(SiteSetting.key == key).first()
        if not existing:
            db.add(SiteSetting(key=key, value=val, description=f"Store configuration: {key}"))
    db.commit()

    # 3. Product Reviews
    if db.query(ProductReview).count() == 0:
        demo_user = db.query(User).filter(User.role == UserRole.CUSTOMER.value).first()
        p1 = db.query(Product).filter(Product.id == 1).first()
        p2 = db.query(Product).filter(Product.id == 2).first()
        p5 = db.query(Product).filter(Product.id == 5).first()
        
        if demo_user:
            reviews = []
            if p1:
                reviews.append(ProductReview(
                    product_id=p1.id,
                    user_id=demo_user.id,
                    rating=5,
                    headline="Instant code delivery!",
                    comment="Redeemed the $50 Steam code within 5 seconds of payment. Worked flawlessly on my Steam account.",
                    is_verified_purchase=True,
                    is_approved=True
                ))
            if p2:
                reviews.append(ProductReview(
                    product_id=p2.id,
                    user_id=demo_user.id,
                    rating=5,
                    headline="Legit PlayStation card",
                    comment="Added $25 to my US PSN wallet without issues. Much cheaper than other online retailers.",
                    is_verified_purchase=True,
                    is_approved=True
                ))
            if p5:
                reviews.append(ProductReview(
                    product_id=p5.id,
                    user_id=demo_user.id,
                    rating=5,
                    headline="Activated Windows 11 Pro immediately",
                    comment="Genuine Microsoft retail key. BitLocker and remote desktop enabled instantly. Great seller!",
                    is_verified_purchase=True,
                    is_approved=True
                ))
            if reviews:
                db.add_all(reviews)
                db.commit()

    # 4. Remove any online payment gateways (Stripe, PayPal, Crypto) to enforce Wallet, D17 & Bank Transfer only
    db.query(PaymentGateway).filter(PaymentGateway.code.in_(["stripe", "paypal", "crypto"])).delete(synchronize_session=False)
    db.commit()

    # 5. Ensure Nexus Wallet / Store Credit Payment Gateway
    wallet_gw = db.query(PaymentGateway).filter(PaymentGateway.code == "wallet").first()
    if not wallet_gw:
        db.add(PaymentGateway(
            name="Nexus Wallet / Store Credit",
            code="wallet",
            is_enabled=True,
            fee_percentage=0.0,
            instructions="1-Click Instant Payment using your pre-funded Nexus store credit balance. Zero fees.",
            display_order=1
        ))
    else:
        wallet_gw.display_order = 1
        wallet_gw.is_enabled = True

    # 6. Ensure D17 / Flouci Gateway
    d17_gw = db.query(PaymentGateway).filter(PaymentGateway.code == "d17").first()
    if not d17_gw:
        db.add(PaymentGateway(
            name="Paiement Mobile D17 / Flouci",
            code="d17",
            is_enabled=True,
            fee_percentage=0.0,
            instructions="Paiement Mobile D17 : Envoyez au Numéro : +216 98 765 432 (Titulaire : NEXUS KEYS). Joignez votre capture d'écran ci-dessous pour confirmation rapide.",
            display_order=2
        ))
    else:
        d17_gw.display_order = 2
        d17_gw.is_enabled = True

    # 7. Ensure Virement Bancaire Direct (R.I.B)
    bank_gw = db.query(PaymentGateway).filter(PaymentGateway.code == "bank_transfer").first()
    if not bank_gw:
        db.add(PaymentGateway(
            name="Virement Bancaire Direct (R.I.B)",
            code="bank_transfer",
            is_enabled=True,
            fee_percentage=0.0,
            instructions="Virement Bancaire Officiel : Banque BIAT / Attijari | R.I.B : 08 015 00011122233344 55 | Titulaire : NEXUS DIGITAL STORE. Joignez votre reçu pour validation.",
            display_order=3
        ))
    else:
        bank_gw.name = "Virement Bancaire Direct (R.I.B)"
        bank_gw.instructions = "Virement Bancaire Officiel : Banque BIAT / Attijari | R.I.B : 08 015 00011122233344 55 | Titulaire : NEXUS DIGITAL STORE. Joignez votre reçu pour validation."
        bank_gw.display_order = 3
        bank_gw.is_enabled = True
    db.commit()

    # 8. Ensure Demo Customer initial wallet balance & welcome credit
    demo_client = db.query(User).filter(User.email == "client@store.com").first()
    if demo_client and float(demo_client.wallet_balance or 0.0) == 0.0:
        demo_client.wallet_balance = 50.0
        from app.models import WalletTransaction
        db.add(WalletTransaction(
            user_id=demo_client.id,
            transaction_type="topup",
            amount=50.0,
            balance_after=50.0,
            reference="WELCOME-CREDIT-50",
            description="Nexus VIP Welcome Store Credit ($50.00)"
        ))
        db.commit()

    # 7. Ensure Initial Gift Vouchers & Referral Codes
    from app.models import GiftVoucher
    demo_vouchers = [
        ("NXUS-CARD-1000-DEMO", 10.0, "Demo Welcome Cards"),
        ("NXUS-CARD-2500-VIP", 25.0, "VIP Promotional Batch"),
        ("NXUS-CARD-5000-PRO", 50.0, "Executive Rewards")
    ]
    for code, amt, batch in demo_vouchers:
        if not db.query(GiftVoucher).filter(GiftVoucher.code == code).first():
            db.add(GiftVoucher(code=code, amount=amt, batch_name=batch))
    db.commit()

    if demo_client and not demo_client.referral_code:
        demo_client.referral_code = "REF-CLIENT42"
        db.commit()

def seed_database():
    """Initializes tables and seeds default categories, products, keys, and admin accounts."""
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    try:
        # Check if already seeded
        if db.query(User).filter(User.email == "admin@vault.internal").first():
            ensure_auxiliary_seed_data(db)
            print("Database already contains seed data. Auxiliary seeds ensured.")
            return

        print("Seeding Initial Database...")

        # 1. Default Super Admin (Hidden Portal)
        admin_user = User(
            email="admin@vault.internal",
            password_hash=hash_password("admin_vault_2026!"),
            full_name="Master Administrator",
            role=UserRole.SUPER_ADMIN.value,
            is_active=True,
            created_at=datetime.utcnow()
        )
        db.add(admin_user)

        # 2. Demo Customer Account
        demo_client = User(
            email="client@store.com",
            password_hash=hash_password("client123"),
            full_name="Alex Carter",
            role=UserRole.CUSTOMER.value,
            is_active=True,
            created_at=datetime.utcnow()
        )
        db.add(demo_client)
        db.commit()

        # 3. Categories
        cat_cards = Category(
            name="Gaming Gift Cards", 
            slug="gaming-gift-cards", 
            icon="credit-card",
            description="Official digital cards for Steam, PlayStation, Xbox, and Nintendo."
        )
        cat_subs = Category(
            name="Software Subscriptions", 
            slug="software-subscriptions", 
            icon="repeat",
            description="Premium cloud, entertainment, and productivity software subscriptions."
        )
        cat_keys = Category(
            name="Game & OS Keys", 
            slug="game-os-keys", 
            icon="key",
            description="Genuine lifetime activation keys for games and operating systems."
        )
        cat_vpn = Category(
            name="VPN & Cybersecurity", 
            slug="vpn-cybersecurity", 
            icon="shield",
            description="Encrypted privacy and antivirus digital licenses."
        )
        db.add_all([cat_cards, cat_subs, cat_keys, cat_vpn])
        db.commit()

        # 4. Products
        p1 = Product(
            title="Steam Wallet Card $50 USD",
            slug="steam-wallet-50-usd",
            category_id=cat_cards.id,
            platform="Steam / PC",
            region="GLOBAL",
            price=49.99,
            cost_price=43.50,
            badge="HOT",
            description="Instantly top-up your Steam account with $50 credit. Redeemable worldwide without expiration.",
            image_url="https://images.unsplash.com/photo-1612287232230-e3778dfc5c70?auto=format&fit=crop&w=600&q=80"
        )
        p2 = Product(
            title="PlayStation Network Card $25 USD",
            slug="psn-card-25-usd",
            category_id=cat_cards.id,
            platform="PlayStation 4 / 5",
            region="US",
            price=24.99,
            cost_price=21.80,
            badge="BESTSELLER",
            description="Official PlayStation store digital code for games, DLCs, and PS Plus passes.",
            image_url="https://images.unsplash.com/photo-1606144042614-b2417e99c4e3?auto=format&fit=crop&w=600&q=80"
        )
        p3 = Product(
            title="Xbox Game Pass Ultimate - 3 Months",
            slug="xbox-game-pass-ultimate-3m",
            category_id=cat_cards.id,
            platform="Xbox & PC",
            region="GLOBAL",
            price=42.50,
            cost_price=35.00,
            badge="POPULAR",
            description="Includes hundreds of high-quality games, cloud gaming, and EA Play membership.",
            image_url="https://images.unsplash.com/photo-1621259182978-fbf93132d53d?auto=format&fit=crop&w=600&q=80"
        )
        p4 = Product(
            title="Spotify Premium 12 Months Individual",
            slug="spotify-premium-12-months",
            category_id=cat_subs.id,
            platform="All Devices",
            region="GLOBAL",
            price=39.99,
            cost_price=27.50,
            badge="INSTANT",
            description="Ad-free music streaming, offline downloads, and unlimited skips for a full year.",
            image_url="https://images.unsplash.com/photo-1614680376593-902f749f7ffc?auto=format&fit=crop&w=600&q=80"
        )
        p5 = Product(
            title="Windows 11 Professional Retail Key",
            slug="windows-11-pro-retail",
            category_id=cat_keys.id,
            platform="PC",
            region="GLOBAL",
            price=19.99,
            cost_price=7.50,
            badge="TOP SELLER",
            description="Genuine Microsoft lifetime activation key for 1 PC with BitLocker and remote desktop.",
            image_url="https://images.unsplash.com/photo-1593642632823-8f785ba67e45?auto=format&fit=crop&w=600&q=80"
        )
        p6 = Product(
            title="NordVPN 1-Year Ultra Secure License",
            slug="nordvpn-1-year-plan",
            category_id=cat_vpn.id,
            platform="Windows / Mac / iOS / Android",
            region="GLOBAL",
            price=48.00,
            cost_price=31.00,
            badge="SECURE",
            description="Military-grade encryption, threat protection, and lightning-fast speeds for 6 devices.",
            image_url="https://images.unsplash.com/photo-1563986768609-322da13575f3?auto=format&fit=crop&w=600&q=80"
        )

        db.add_all([p1, p2, p3, p4, p5, p6])
        db.commit()

        # 5. Populate Digital License Keys in Inventory (Immediate Available Stock)
        keys = [
            # Steam Keys
            DigitalKey(product_id=p1.id, key_code="STM-9X47-V62Q-LR88", status=KeyStatus.AVAILABLE.value),
            DigitalKey(product_id=p1.id, key_code="STM-1K93-B87M-QQ41", status=KeyStatus.AVAILABLE.value),
            DigitalKey(product_id=p1.id, key_code="STM-5C82-P04Z-WN77", status=KeyStatus.AVAILABLE.value),
            # PSN Keys
            DigitalKey(product_id=p2.id, key_code="PSN-US-8924-1184-7721", status=KeyStatus.AVAILABLE.value),
            DigitalKey(product_id=p2.id, key_code="PSN-US-4431-9023-5512", status=KeyStatus.AVAILABLE.value),
            # Xbox Keys
            DigitalKey(product_id=p3.id, key_code="XGP-3M-TR99-8812-ZZ34", status=KeyStatus.AVAILABLE.value),
            DigitalKey(product_id=p3.id, key_code="XGP-3M-AA11-9922-KK00", status=KeyStatus.AVAILABLE.value),
            # Spotify
            DigitalKey(product_id=p4.id, key_code="SPOT-1Y-PREM-8891-2401", status=KeyStatus.AVAILABLE.value),
            DigitalKey(product_id=p4.id, key_code="SPOT-1Y-PREM-7731-5510", status=KeyStatus.AVAILABLE.value),
            # Windows 11
            DigitalKey(product_id=p5.id, key_code="W11P-VK7JG-NPHTM-C97JM-9MPGT", status=KeyStatus.AVAILABLE.value),
            DigitalKey(product_id=p5.id, key_code="W11P-DPH2V-TTNVB-4X9Q3-TJR4H", status=KeyStatus.AVAILABLE.value),
            DigitalKey(product_id=p5.id, key_code="W11P-NW6C2-QMPVW-D7KKK-3GKT6", status=KeyStatus.AVAILABLE.value),
            # NordVPN
            DigitalKey(product_id=p6.id, key_code="NORD-1Y-ULTRA-92810-SAFE", status=KeyStatus.AVAILABLE.value),
            DigitalKey(product_id=p6.id, key_code="NORD-1Y-ULTRA-54129-FAST", status=KeyStatus.AVAILABLE.value),
        ]
        db.add_all(keys)
        db.commit()

        # 6. Payment Gateways (Exclusive: Wallet Credit, D17 Mobile, and Bank Wire R.I.B)
        gateways = [
            PaymentGateway(
                name="Nexus Wallet / Store Credit",
                code="wallet",
                is_enabled=True,
                fee_percentage=0.0,
                instructions="1-Click Instant Payment using your pre-funded Nexus store credit balance. Zero fees.",
                display_order=1
            ),
            PaymentGateway(
                name="Paiement Mobile D17 / Flouci",
                code="d17",
                is_enabled=True,
                fee_percentage=0.0,
                instructions="Paiement Mobile D17 : Envoyez au Numéro : +216 98 765 432 (Titulaire : NEXUS KEYS). Joignez votre capture d'écran ci-dessous pour confirmation rapide.",
                display_order=2
            ),
            PaymentGateway(
                name="Virement Bancaire Direct (R.I.B)",
                code="bank_transfer",
                is_enabled=True,
                fee_percentage=0.0,
                instructions="Virement Bancaire Officiel : Banque BIAT / Attijari | R.I.B : 08 015 00011122233344 55 | Titulaire : NEXUS DIGITAL STORE. Joignez votre reçu pour validation.",
                display_order=3
            )
        ]
        db.add_all(gateways)

        # 7. Site Settings
        settings = [
            SiteSetting(key="store_name", value="NEXUS KEYS", description="Public store brand name"),
            SiteSetting(key="currency_symbol", value="$", description="Store currency symbol"),
            SiteSetting(key="support_email", value="support@nexuskeys.store", description="Customer support email"),
            SiteSetting(key="admin_secret_path", value="/portal-vault-admin", description="Secret Admin Access URL")
        ]
        db.add_all(settings)
        db.commit()

        print("Database seeded successfully with initial products, keys, and credentials.")

    finally:
        db.close()

if __name__ == "__main__":
    seed_database()
