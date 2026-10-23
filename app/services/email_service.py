"""
NEXUS KEYS - Automated Customer Email Receipt & Key Dispatch Service
Dispatches professional HTML order receipts with genuine digital keys to clients upon payment confirmation.
Supports live SMTP (Gmail, SendGrid, Mailgun, Amazon SES) with graceful fallback to archived local logging.
"""

import smtplib
import ssl
import json
import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy.orm import Session
from app.config import DATA_DIR
from app.models import Order, SiteSetting

SENT_RECEIPTS_DIR = DATA_DIR / "sent_receipts"
SENT_EMAILS_LOG = DATA_DIR / "sent_emails.jsonl"

try:
    SENT_RECEIPTS_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    pass

def get_smtp_config(db: Session) -> dict:
    """Retrieves current SMTP and branding configuration from site_settings table or .env."""
    records = db.query(SiteSetting).all()
    settings = {s.key: s.value for s in records}
    
    host = (settings.get("smtp_host") or os.getenv("SMTP_HOST", "")).strip()
    port_val = (settings.get("smtp_port") or os.getenv("SMTP_PORT", "587")).strip()
    user = (settings.get("smtp_user") or os.getenv("SMTP_USER", "")).strip()
    password = (settings.get("smtp_password") or os.getenv("SMTP_PASSWORD", "")).strip()
    from_email = (settings.get("smtp_from_email") or settings.get("support_email") or os.getenv("SMTP_FROM_EMAIL", "orders@nexuskeys.io")).strip()
    from_name = (settings.get("store_name") or os.getenv("SMTP_FROM_NAME", "NEXUS KEYS")).strip()
    tls_val = (settings.get("smtp_use_tls") or os.getenv("SMTP_USE_TLS", "true")).strip().lower()

    try:
        port = int(port_val)
    except (ValueError, TypeError):
        port = 587

    return {
        "host": host,
        "port": port,
        "user": user,
        "password": password,
        "from_email": from_email,
        "from_name": from_name,
        "use_tls": tls_val in ["true", "1", "yes", "on"]
    }

def build_order_receipt_html(order: Order, store_name: str, support_email: str) -> str:
    """Renders high-conversion, responsive dark-cyber HTML email receipt."""
    date_str = order.created_at.strftime("%B %d, %Y - %H:%M UTC") if order.created_at else "Just Now"

    # Compile delivered keys
    items_html = ""
    for item in order.items:
        # Extract keys
        keys_list = [k.key_code for k in order.assigned_keys if k.product_id == item.product_id]
        if not keys_list and item.delivered_keys_json:
            try:
                keys_list = json.loads(item.delivered_keys_json)
            except Exception:
                keys_list = []

        keys_block = ""
        if keys_list:
            keys_cards = "".join([
                f"""<div style="background-color: #0d1117; border: 1px solid #10b981; border-radius: 6px; padding: 10px 14px; margin-top: 6px; font-family: 'Courier New', monospace; font-size: 14px; font-weight: bold; color: #34d399; letter-spacing: 1px;">
                    {k}
                </div>""" for k in keys_list
            ])
            keys_block = f"""
            <div style="margin-top: 8px;">
                <span style="font-size: 11px; font-weight: bold; color: #94a3b8; text-transform: uppercase;">License Key / Activation Code:</span>
                {keys_cards}
            </div>
            """
        else:
            keys_block = """
            <div style="margin-top: 6px; font-size: 12px; color: #94a3b8; font-style: italic;">
                Keys accessible directly from your client vault.
            </div>
            """

        items_html += f"""
        <tr style="border-bottom: 1px solid #1e293b;">
            <td style="padding: 14px 10px; vertical-align: top;">
                <div style="font-weight: bold; color: #ffffff; font-size: 14px;">{item.product.title}</div>
                <div style="font-size: 11px; color: #06b6d4; text-transform: uppercase; font-weight: 600; margin-top: 2px;">{item.product.platform} &bull; {item.product.region}</div>
                {keys_block}
            </td>
            <td style="padding: 14px 10px; text-align: center; color: #cbd5e1; font-size: 13px; vertical-align: top;">
                {item.quantity}
            </td>
            <td style="padding: 14px 10px; text-align: right; color: #ffffff; font-weight: bold; font-size: 14px; vertical-align: top; font-family: 'Courier New', monospace;">
                ${(item.unit_price * item.quantity):.2f}
            </td>
        </tr>
        """

    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>Order Confirmation #{order.order_number}</title>
    </head>
    <body style="margin: 0; padding: 20px 0; background-color: #07090e; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #e2e8f0;">
        <table align="center" width="600" cellpadding="0" cellspacing="0" style="max-width: 600px; width: 100%; background-color: #0f172a; border: 1px solid #1e293b; border-radius: 16px; overflow: hidden; margin: 0 auto; box-shadow: 0 10px 25px rgba(0,0,0,0.5);">
            <!-- Header -->
            <tr>
                <td style="background: linear-gradient(135deg, #090d16 0%, #1e1b4b 100%); padding: 30px; text-align: center; border-bottom: 1px solid #334155;">
                    <div style="font-size: 20px; font-weight: 900; letter-spacing: 2px; color: #ffffff; font-family: 'Courier New', monospace;">
                        NEXUS<span style="color: #f43f5e;">KEYS</span>
                    </div>
                    <div style="display: inline-block; margin-top: 12px; background-color: rgba(16, 185, 129, 0.15); border: 1px solid #10b981; border-radius: 20px; padding: 4px 14px; font-size: 11px; font-weight: 800; color: #34d399; text-transform: uppercase; letter-spacing: 1px;">
                        &#10003; Payment Confirmed & Keys Dispatched
                    </div>
                    <h1 style="color: #ffffff; font-size: 22px; margin: 16px 0 4px 0; font-weight: 800;">Official Order Receipt</h1>
                    <div style="color: #94a3b8; font-size: 13px;">Order <strong style="color: #38bdf8; font-family: 'Courier New', monospace;">{order.order_number}</strong> &bull; {date_str}</div>
                </td>
            </tr>

            <!-- Client Greeting -->
            <tr>
                <td style="padding: 24px 30px 10px 30px;">
                    <p style="margin: 0; font-size: 14px; color: #cbd5e1; line-height: 1.6;">
                        Hello <strong style="color: #ffffff;">{order.user.full_name}</strong>,<br>
                        Thank you for your purchase at <strong>{store_name}</strong>! Your payment via <strong>{order.payment_method}</strong> has been verified. Your genuine digital licenses have been unsealed and added to your permanent vault.
                    </p>
                </td>
            </tr>

            <!-- Line Items Table -->
            <tr>
                <td style="padding: 10px 30px;">
                    <table width="100%" cellpadding="0" cellspacing="0" style="margin-top: 10px; border-collapse: collapse;">
                        <thead>
                            <tr style="border-bottom: 2px solid #334155; text-align: left; font-size: 11px; text-transform: uppercase; color: #94a3b8; font-family: 'Courier New', monospace;">
                                <th style="padding: 10px 10px;">Item / License Key</th>
                                <th style="padding: 10px 10px; text-align: center;">Qty</th>
                                <th style="padding: 10px 10px; text-align: right;">Total</th>
                            </tr>
                        </thead>
                        <tbody>
                            {items_html}
                        </tbody>
                    </table>
                </td>
            </tr>

            <!-- Grand Total -->
            <tr>
                <td style="padding: 15px 30px;">
                    <table width="100%" cellpadding="0" cellspacing="0" style="background-color: #07090e; border: 1px solid #1e293b; border-radius: 12px; padding: 16px;">
                        <tr>
                            <td style="font-size: 13px; color: #94a3b8; padding: 4px 0;">Payment Method:</td>
                            <td style="font-size: 13px; font-weight: bold; color: #ffffff; text-align: right; padding: 4px 0;">{order.payment_method}</td>
                        </tr>
                        <tr>
                            <td style="font-size: 13px; color: #94a3b8; padding: 4px 0;">Payment Status:</td>
                            <td style="font-size: 12px; font-weight: 800; color: #34d399; text-transform: uppercase; text-align: right; padding: 4px 0;">Completed &amp; Verified</td>
                        </tr>
                        <tr style="border-top: 1px solid #1e293b;">
                            <td style="font-size: 15px; font-weight: 800; color: #ffffff; padding: 10px 0 4px 0;">Total Paid:</td>
                            <td style="font-size: 18px; font-weight: 900; color: #38bdf8; text-align: right; padding: 10px 0 4px 0; font-family: 'Courier New', monospace;">${order.total_amount:.2f} USD</td>
                        </tr>
                    </table>
                </td>
            </tr>

            <!-- Vault CTA Button -->
            <tr>
                <td style="padding: 10px 30px 25px 30px; text-align: center;">
                    <a href="http://127.0.0.1:8000/profile#vault" style="display: inline-block; background: linear-gradient(135deg, #06b6d4 0%, #2563eb 100%); color: #000000; font-weight: 800; font-size: 13px; text-transform: uppercase; text-decoration: none; padding: 12px 28px; border-radius: 10px; letter-spacing: 1px; box-shadow: 0 4px 15px rgba(6, 182, 212, 0.4);">
                        Open My Digital Key Vault
                    </a>
                    <div style="font-size: 11px; color: #64748b; margin-top: 10px;">
                        Access your keys anytime with instant one-click copying and redemption instructions.
                    </div>
                </td>
            </tr>

            <!-- Footer -->
            <tr>
                <td style="background-color: #07090e; padding: 20px 30px; text-align: center; border-top: 1px solid #1e293b; font-size: 11px; color: #64748b;">
                    <div>Questions or redemption help? Contact our 24/7 team at <a href="mailto:{support_email}" style="color: #38bdf8; text-decoration: none;">{support_email}</a></div>
                    <div style="margin-top: 6px;">&copy; 2026 {store_name}. All genuine digital licenses backed by authenticity guarantee.</div>
                </td>
            </tr>
        </table>
    </body>
    </html>
    """
    return html

def send_order_receipt_email(order: Order, db: Session) -> dict:
    """
    Compiles and dispatches official order receipt with license keys to customer.
    Tries configured SMTP first; logs to data/sent_emails.jsonl and archives HTML.
    """
    if not order or not order.user or not order.user.email:
        return {"success": False, "error": "Invalid order or recipient"}

    config = get_smtp_config(db)
    recipient = order.user.email
    recipient_name = order.user.full_name
    subject = f"Your {config['from_name']} Order Receipt & Keys [#{order.order_number}]"

    html_content = build_order_receipt_html(
        order=order,
        store_name=config["from_name"],
        support_email=config["from_email"]
    )

    # Save local archived copy for audit & immediate preview
    archive_file = SENT_RECEIPTS_DIR / f"{order.order_number}.html"
    try:
        archive_file.write_text(html_content, encoding="utf-8")
    except Exception as e:
        print(f"[EmailService] Could not archive receipt HTML: {e}")

    # Generate and archive official PDF invoice
    pdf_bytes = None
    try:
        from app.services.invoice_service import generate_order_invoice_pdf
        pdf_bytes = generate_order_invoice_pdf(order, db, cache_to_disk=True)
    except Exception as pdf_err:
        print(f"[EmailService] Could not pre-generate PDF invoice: {pdf_err}")

    dispatch_record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "order_id": order.id,
        "order_number": order.order_number,
        "recipient": recipient,
        "recipient_name": recipient_name,
        "subject": subject,
        "total_amount": order.total_amount,
        "payment_method": order.payment_method,
        "archive_file": str(archive_file),
        "pdf_invoice_generated": pdf_bytes is not None,
        "status": "pending"
    }

    # Attempt SMTP delivery if credentials provided
    smtp_sent = False
    smtp_error = None

    if config["host"] and config["user"] and config["password"]:
        try:
            msg = MIMEMultipart("mixed")
            msg["Subject"] = subject
            msg["From"] = f"{config['from_name']} <{config['from_email']}>"
            msg["To"] = f"{recipient_name} <{recipient}>"

            alt_body = MIMEMultipart("alternative")
            text_fallback = f"Order Confirmation #{order.order_number}\nTotal Paid: ${order.total_amount:.2f}\nPlease view your keys in your vault at: http://127.0.0.1:8000/profile#vault"
            alt_body.attach(MIMEText(text_fallback, "plain"))
            alt_body.attach(MIMEText(html_content, "html"))
            msg.attach(alt_body)

            if pdf_bytes:
                pdf_attachment = MIMEApplication(pdf_bytes, _subtype="pdf")
                pdf_attachment.add_header("Content-Disposition", "attachment", filename=f"Invoice-{order.order_number}.pdf")
                msg.attach(pdf_attachment)

            if config["port"] == 465:
                context = ssl.create_default_context()
                with smtplib.SMTP_SSL(config["host"], config["port"], context=context, timeout=10) as server:
                    server.login(config["user"], config["password"])
                    server.send_message(msg)
            else:
                with smtplib.SMTP(config["host"], config["port"], timeout=10) as server:
                    if config["use_tls"]:
                        server.starttls()
                    server.login(config["user"], config["password"])
                    server.send_message(msg)

            smtp_sent = True
            dispatch_record["status"] = "delivered_smtp"
        except Exception as ex:
            smtp_error = str(ex)
            dispatch_record["status"] = "failed_smtp_fallback_archived"
            dispatch_record["smtp_error"] = smtp_error
            print(f"[EmailService] Live SMTP send failed ({smtp_error}). Archived copy preserved.")
    else:
        # Dev / Offline mode: recorded to local dispatch vault
        dispatch_record["status"] = "mock_delivered_local"

    # Append to log file
    try:
        with open(SENT_EMAILS_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(dispatch_record) + "\n")
    except Exception as e:
        print(f"[EmailService] Could not write sent_emails.jsonl: {e}")

    return {
        "success": True,
        "order_number": order.order_number,
        "recipient": recipient,
        "smtp_sent": smtp_sent,
        "smtp_error": smtp_error,
        "archive_path": str(archive_file)
    }

def send_test_email(to_email: str, db: Session) -> dict:
    """Dispatches a test verification email using active SMTP configuration."""
    config = get_smtp_config(db)
    subject = f"Test Email Connection from {config['from_name']}"
    
    html = f"""
    <div style="font-family: sans-serif; padding: 20px; background-color: #0f172a; color: #f8fafc; border-radius: 10px;">
        <h2 style="color: #38bdf8;">SMTP Configuration Verified Successfully</h2>
        <p>This is a test notification confirming that email receipts from <strong>{config['from_name']}</strong> are configured and delivering correctly.</p>
        <p style="font-size: 12px; color: #94a3b8;">Sent via {config['host']}:{config['port']} at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}</p>
    </div>
    """

    if not (config["host"] and config["user"] and config["password"]):
        return {
            "success": False,
            "error": "SMTP Host, Username, or Password are not configured in Admin Settings."
        }

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"{config['from_name']} <{config['from_email']}>"
        msg["To"] = to_email
        msg.attach(MIMEText("Test email connection successful.", "plain"))
        msg.attach(MIMEText(html, "html"))

        if config["port"] == 465:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(config["host"], config["port"], context=context, timeout=10) as server:
                server.login(config["user"], config["password"])
                server.send_message(msg)
        else:
            with smtplib.SMTP(config["host"], config["port"], timeout=10) as server:
                if config["use_tls"]:
                    server.starttls()
                server.login(config["user"], config["password"])
                server.send_message(msg)

        return {"success": True, "message": f"Test email sent successfully to {to_email}."}
    except Exception as e:
        return {"success": False, "error": f"SMTP dispatch failed: {str(e)}"}

# ==========================================
# RESTOCK RADAR AUTOMATED DISPATCHER
# ==========================================

def build_restock_alert_html(user, product, store_name: str, support_email: str) -> str:
    """Builds a high-impact, cyber-dark HTML email notifying customer of product restock."""
    return f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="utf-8"></head>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #0b0f19; margin: 0; padding: 24px; color: #f8fafc;">
        <div style="max-width: 600px; margin: 0 auto; background-color: #111827; border: 1px solid #1f2937; border-radius: 16px; overflow: hidden; box-shadow: 0 10px 25px rgba(0,0,0,0.5);">
            <div style="background: linear-gradient(135deg, #0284c7 0%, #4f46e5 100%); padding: 24px; text-align: center;">
                <h1 style="color: #ffffff; margin: 0; font-size: 20px; font-weight: 900; letter-spacing: 1px;">{store_name} RESTOCK RADAR</h1>
                <p style="color: #e0f2fe; margin: 6px 0 0 0; font-size: 13px;">Your requested item has been replenished!</p>
            </div>
            <div style="padding: 24px;">
                <p style="font-size: 14px; color: #cbd5e1; margin-top: 0;">Hello <strong>{user.full_name}</strong>,</p>
                <p style="font-size: 13px; color: #94a3b8; line-height: 1.6;">
                    Great news! A product you saved to your personal Wishlist is back in stock with fresh genuine license keys ready for instant delivery.
                </p>
                <div style="background-color: #0f172a; border: 1px solid #334155; border-radius: 12px; padding: 16px; margin: 20px 0;">
                    <div style="font-size: 16px; font-weight: bold; color: #ffffff;">{product.title}</div>
                    <div style="font-size: 12px; color: #38bdf8; margin-top: 4px;">Platform: {product.platform or 'Global'} &bull; Region: {product.region or 'GLOBAL'}</div>
                    <div style="font-size: 18px; font-weight: 900; color: #34d399; margin-top: 8px;">${product.price:.2f} USD</div>
                    <div style="font-size: 11px; color: #10b981; margin-top: 4px;">⚡ Instant Digital Key Dispatch Ready ({product.available_keys_count} available)</div>
                </div>
                <div style="text-align: center; margin: 28px 0;">
                    <a href="http://127.0.0.1:8000/product/{product.slug}" style="display: inline-block; background: linear-gradient(to right, #06b6d4, #2563eb); color: #000000; font-weight: 900; font-size: 13px; text-decoration: none; padding: 12px 28px; border-radius: 10px; text-transform: uppercase; letter-spacing: 0.5px;">
                        Claim Your Key Now &rarr;
                    </a>
                </div>
                <p style="font-size: 11px; color: #64748b; text-align: center; border-top: 1px solid #1f2937; padding-top: 16px; margin-bottom: 0;">
                    You received this alert because you enabled restock notifications on your {store_name} wishlist. For help, contact {support_email}.
                </p>
            </div>
        </div>
    </body>
    </html>
    """

def send_restock_alert_email(user, product, db: Session) -> bool:
    """Dispatches restock notification email to a specific wishlist subscriber."""
    config = get_smtp_config(db)
    subject = f"⚡ Back in Stock: {product.title} is now available at {config['from_name']}!"
    html_content = build_restock_alert_html(user, product, config["from_name"], config["from_email"])

    # Log to sent_emails.jsonl
    log_entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "type": "RESTOCK_ALERT",
        "to": user.email,
        "product_id": product.id,
        "product_title": product.title,
        "subject": subject
    }
    try:
        with open(SENT_EMAILS_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry) + "\n")
    except Exception as e:
        print(f"[EmailService] Failed to log restock alert: {e}")

    # Attempt live SMTP if configured
    if config["host"] and config["user"] and config["password"]:
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = f"{config['from_name']} <{config['from_email']}>"
            msg["To"] = user.email
            msg.attach(MIMEText(f"{product.title} is back in stock!", "plain"))
            msg.attach(MIMEText(html_content, "html"))
            if config["port"] == 465:
                context = ssl.create_default_context()
                with smtplib.SMTP_SSL(config["host"], config["port"], context=context, timeout=8) as server:
                    server.login(config["user"], config["password"])
                    server.send_message(msg)
            else:
                with smtplib.SMTP(config["host"], config["port"], timeout=8) as server:
                    if config["use_tls"]:
                        server.starttls()
                    server.login(config["user"], config["password"])
                    server.send_message(msg)
            return True
        except Exception as ex:
            print(f"[EmailService] Live SMTP restock alert failed: {ex}")

    return True

def dispatch_product_restock_notifications(product_id: int, new_keys_count: int, db: Session) -> int:
    """
    Finds all customers who bookmarked this product on their wishlist with restock alerts enabled
    and dispatches automated email notifications.
    """
    from app.models import WishlistItem, Product
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product or product.available_keys_count <= 0:
        return 0

    subscribers = db.query(WishlistItem).filter(
        WishlistItem.product_id == product_id,
        WishlistItem.notify_on_restock == True
    ).all()

    dispatched = 0
    for sub in subscribers:
        if sub.user and sub.user.email:
            try:
                send_restock_alert_email(sub.user, product, db)
                dispatched += 1
            except Exception as e:
                print(f"[RestockDispatcher] Error notifying {sub.user.email}: {e}")

    return dispatched

# ==========================================
# PASSWORD RESET EMAIL ENGINE
# ==========================================

def build_password_reset_html(user, reset_url: str, store_name: str, support_email: str) -> str:
    """Renders sleek dark-cyber HTML password reset email."""
    return f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="utf-8"></head>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #0b0f19; margin: 0; padding: 24px; color: #f8fafc;">
        <div style="max-width: 600px; margin: 0 auto; background-color: #111827; border: 1px solid #1f2937; border-radius: 16px; overflow: hidden; box-shadow: 0 10px 25px rgba(0,0,0,0.5);">
            <div style="background: linear-gradient(135deg, #0284c7 0%, #6366f1 100%); padding: 24px; text-align: center;">
                <h1 style="color: #ffffff; margin: 0; font-size: 20px; font-weight: 900; letter-spacing: 1px;">{store_name} SECURITY</h1>
                <p style="color: #e0f2fe; margin: 6px 0 0 0; font-size: 13px;">Password Reset Request</p>
            </div>
            <div style="padding: 24px;">
                <p style="font-size: 14px; color: #cbd5e1; margin-top: 0;">Hello <strong>{user.full_name}</strong>,</p>
                <p style="font-size: 13px; color: #94a3b8; line-height: 1.6;">
                    We received a request to reset the password associated with your account ({user.email}). Click the secure button below to choose a new password:
                </p>
                <div style="text-align: center; margin: 28px 0;">
                    <a href="{reset_url}" style="display: inline-block; background: linear-gradient(to right, #06b6d4, #3b82f6); color: #000000; font-weight: 900; font-size: 13px; text-decoration: none; padding: 14px 32px; border-radius: 12px; text-transform: uppercase; letter-spacing: 0.5px; box-shadow: 0 4px 14px rgba(6, 182, 212, 0.4);">
                        Reset My Password &rarr;
                    </a>
                </div>
                <p style="font-size: 12px; color: #94a3b8; line-height: 1.5;">
                    If the button above does not work, copy and paste this link into your browser:<br>
                    <a href="{reset_url}" style="color: #38bdf8; word-break: break-all; font-family: monospace; font-size: 11px;">{reset_url}</a>
                </p>
                <p style="font-size: 11px; color: #ef4444; margin-top: 20px;">
                    ⚡ This reset link will expire in 30 minutes. If you did not request this change, please ignore this email; your account remains completely secure.
                </p>
                <p style="font-size: 11px; color: #64748b; text-align: center; border-top: 1px solid #1f2937; padding-top: 16px; margin-bottom: 0;">
                    {store_name} Account Security Vault &bull; Questions? Contact {support_email}
                </p>
            </div>
        </div>
    </body>
    </html>
    """

def send_password_reset_email(user, reset_url: str, db: Session) -> bool:
    """Dispatches password reset instructions email."""
    config = get_smtp_config(db)
    subject = f"🔒 Password Reset Instructions — {config['from_name']}"
    html_content = build_password_reset_html(user, reset_url, config["from_name"], config["from_email"])

    log_entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "type": "PASSWORD_RESET",
        "to": user.email,
        "reset_url": reset_url,
        "subject": subject
    }
    try:
        with open(SENT_EMAILS_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry) + "\n")
    except Exception as e:
        print(f"[EmailService] Failed to log password reset email: {e}")

    # Attempt live SMTP if credentials provided
    if config["host"] and config["user"] and config["password"]:
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = f"{config['from_name']} <{config['from_email']}>"
            msg["To"] = user.email
            msg.attach(MIMEText(f"Password reset link: {reset_url}", "plain"))
            msg.attach(MIMEText(html_content, "html"))
            if config["port"] == 465:
                context = ssl.create_default_context()
                with smtplib.SMTP_SSL(config["host"], config["port"], context=context, timeout=8) as server:
                    server.login(config["user"], config["password"])
                    server.send_message(msg)
            else:
                with smtplib.SMTP(config["host"], config["port"], timeout=8) as server:
                    if config["use_tls"]:
                        server.starttls()
                    server.login(config["user"], config["password"])
                    server.send_message(msg)
            return True
        except Exception as ex:
            print(f"[EmailService] Live SMTP password reset failed: {ex}")

    return True
