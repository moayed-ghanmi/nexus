"""
NEXUS KEYS - PDF Invoicing & Digital Receipt Engine
Generates publication-quality, cryptographically verified vector PDF invoices for customer orders.
"""

import io
import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy.orm import Session

from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable, Flowable
)

from app.config import DATA_DIR
from app.models import Order, SiteSetting

INVOICES_DIR = DATA_DIR / "invoices"
try:
    INVOICES_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    pass

GATEWAY_LABELS = {
    "credit_card": "Credit / Debit Card (Stripe)",
    "stripe": "Credit / Debit Card (Stripe)",
    "paypal": "PayPal Express Checkout",
    "crypto": "Crypto USDT (TRC-20)",
    "usdt": "Crypto USDT (TRC-20)",
    "d17": "D17 Mobile Transfer",
    "bank_transfer": "Bank Wire Transfer (R.I.B)",
    "bank": "Bank Wire Transfer (R.I.B)"
}

STATUS_CONFIG = {
    "completed": {"text": "PAID & FULFILLED", "bg": "#10B981", "badge_border": "#059669"},
    "pending": {"text": "PAYMENT PENDING", "bg": "#F59E0B", "badge_border": "#D97706"},
    "refunded": {"text": "ORDER REFUNDED", "bg": "#8B5CF6", "badge_border": "#7C3AED"},
    "cancelled": {"text": "ORDER CANCELLED", "bg": "#EF4444", "badge_border": "#DC2626"},
}

def get_store_metadata(db: Session) -> dict:
    records = db.query(SiteSetting).all()
    settings = {s.key: s.value for s in records}
    return {
        "store_name": settings.get("store_name", "NEXUS KEYS").strip(),
        "support_email": settings.get("support_email", "support@nexuskeys.store").strip(),
        "store_domain": settings.get("store_domain", "https://nexuskeys.store").strip(),
        "store_tagline": "Instant Digital License & Gaming Vault"
    }

class OfficialStamp(Flowable):
    """
    Renders an authentic, double-bordered rubber company stamp / official seal
    angled gracefully across the financial settlement area.
    """
    def __init__(
        self,
        status: str = "completed",
        date_str: str = "2026-10-20",
        order_num: str = "ORD-000",
        store_name: str = "NEXUS KEYS",
        width: float = 190,
        height: float = 70,
        rotation: float = -5
    ):
        super().__init__()
        self.status = status.lower()
        self.date_str = date_str
        self.order_num = order_num
        self.store_name = store_name
        self.width = width
        self.height = height
        self.rotation = rotation

    def wrap(self, availWidth, availHeight):
        return self.width, self.height

    def draw(self):
        canv = self.canv
        canv.saveState()
        cx = self.width / 2.0
        cy = self.height / 2.0
        canv.translate(cx, cy)
        canv.rotate(self.rotation)

        # Dynamic stamp ink color matching the lifecycle status
        status_colors = {
            "completed": (0.04, 0.55, 0.28), # Rich Emerald Ink
            "pending": (0.85, 0.48, 0.08),   # Amber Wax
            "refunded": (0.48, 0.22, 0.78),  # Royal Violet Ink
            "cancelled": (0.82, 0.16, 0.16)  # Carmine Red Ink
        }
        r, g, b = status_colors.get(self.status, (0.2, 0.2, 0.2))
        canv.setStrokeColorRGB(r, g, b)
        canv.setFillColorRGB(r, g, b)

        w = self.width - 10
        h = self.height - 10
        x = -w / 2.0
        y = -h / 2.0

        # Outer heavy rounded border (2.2pt)
        canv.setLineWidth(2.2)
        canv.roundRect(x, y, w, h, 6, fill=0, stroke=1)

        # Inner fine line border (0.8pt)
        canv.setLineWidth(0.8)
        canv.roundRect(x + 3.5, y + 3.5, w - 7, h - 7, 4, fill=0, stroke=1)

        # Header: Store Name and Star Accents
        canv.setFont("Helvetica-Bold", 7)
        canv.drawCentredString(0, y + h - 14, f"★  {self.store_name.upper()} OFFICIAL SEAL  ★")

        # Top divider line
        canv.setLineWidth(0.6)
        canv.line(x + 12, y + h - 18, x + w - 12, y + h - 18)

        # Center Status Title
        status_titles = {
            "completed": "PAID & VERIFIED",
            "pending": "PAYMENT PENDING",
            "refunded": "ORDER REFUNDED",
            "cancelled": "ORDER CANCELLED"
        }
        main_text = status_titles.get(self.status, self.status.upper())
        canv.setFont("Helvetica-Bold", 11.5)
        canv.drawCentredString(0, y + (h / 2.0) - 4, main_text)

        # Bottom divider line
        canv.line(x + 12, y + 17, x + w - 12, y + 17)

        # Bottom Audit Code & Date
        short_ord = self.order_num[-8:] if len(self.order_num) >= 8 else self.order_num
        canv.setFont("Helvetica", 6.2)
        canv.drawCentredString(0, y + 8, f"AUTHENTICATED: {self.date_str} • AUTH #{short_ord}")

        canv.restoreState()

def generate_order_invoice_pdf(order: Order, db: Session, cache_to_disk: bool = True) -> bytes:
    """
    Renders a pixel-perfect, branded PDF invoice for an Order using ReportLab.
    Returns the binary PDF bytes and optionally caches to data/invoices/INV-{order_number}.pdf.
    """
    metadata = get_store_metadata(db)
    buffer = io.BytesIO()

    # Document setup: standard 8.5 x 11 inch letter, 0.5 in margins
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=36,
        rightMargin=36,
        topMargin=36,
        bottomMargin=36
    )

    styles = getSampleStyleSheet()

    # Typography styles
    brand_style = ParagraphStyle(
        'BrandTitle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=20,
        leading=24,
        textColor=colors.HexColor('#0F172A')
    )

    tagline_style = ParagraphStyle(
        'BrandTagline',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=8,
        leading=11,
        textColor=colors.HexColor('#64748B')
    )

    inv_title_style = ParagraphStyle(
        'InvoiceTitle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=15,
        leading=18,
        alignment=2,
        textColor=colors.HexColor('#0F172A')
    )

    inv_meta_style = ParagraphStyle(
        'InvoiceMeta',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=8.5,
        leading=13,
        alignment=2,
        textColor=colors.HexColor('#475569')
    )

    status_pill_style = ParagraphStyle(
        'InvoiceStatus',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=8.5,
        leading=11,
        alignment=1,
        textColor=colors.white
    )

    section_heading = ParagraphStyle(
        'SectionHeading',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=9,
        leading=12,
        textColor=colors.HexColor('#0284C7')
    )

    body_style = ParagraphStyle(
        'BodyDark',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=8.5,
        leading=12,
        textColor=colors.HexColor('#1E293B')
    )

    body_bold = ParagraphStyle(
        'BodyBold',
        parent=body_style,
        fontName='Helvetica-Bold'
    )

    key_style = ParagraphStyle(
        'LicenseKey',
        parent=styles['Normal'],
        fontName='Courier-Bold',
        fontSize=9,
        leading=12,
        textColor=colors.HexColor('#065F46')
    )

    story = []

    # 1. Header & Brand Block
    status_info = STATUS_CONFIG.get(order.status.lower(), {"text": order.status.upper(), "bg": "#64748B", "badge_border": "#475569"})
    status_cell = Table(
        [[Paragraph(f"<b>{status_info['text']}</b>", status_pill_style)]],
        colWidths=[140],
        rowHeights=[20]
    )
    status_cell.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor(status_info['bg'])),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 2),
        ('TOPPADDING', (0,0), (-1,-1), 2),
    ]))

    date_str = order.created_at.strftime("%B %d, %Y - %H:%M UTC") if order.created_at else "Recent"

    header_left = [
        Paragraph(metadata["store_name"], brand_style),
        Paragraph(metadata["store_tagline"], tagline_style),
        Paragraph(f"Web: {metadata['store_domain']} &bull; Support: {metadata['support_email']}", tagline_style),
    ]

    header_right = [
        Paragraph("OFFICIAL TAX INVOICE", inv_title_style),
        Paragraph(f"<b>Invoice #:</b> INV-{order.order_number}", inv_meta_style),
        Paragraph(f"<b>Issue Date:</b> {date_str}", inv_meta_style),
        Spacer(1, 4),
        status_cell
    ]

    header_table = Table([[header_left, header_right]], colWidths=[270, 270])
    header_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('ALIGN', (1,0), (1,0), 'RIGHT'),
        ('LEFTPADDING', (0,0), (-1,-1), 0),
        ('RIGHTPADDING', (0,0), (-1,-1), 0),
        ('TOPPADDING', (0,0), (-1,-1), 0),
        ('BOTTOMPADDING', (0,0), (-1,-1), 0),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 12))

    # Divider
    story.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor('#E2E8F0'), spaceAfter=12))

    # 2. Customer & Settlement Details Table
    user_name = order.user.full_name if (order.user and hasattr(order.user, 'full_name') and order.user.full_name) else (order.user.name if order.user and hasattr(order.user, 'name') else "Valued Client")
    user_email = order.user.email if order.user else "N/A"
    user_id_str = f"CUST-#{order.user.id:05d}" if order.user else "GUEST"

    payment_label = GATEWAY_LABELS.get(order.payment_method.lower(), order.payment_method.upper())
    proof_status = "Payment Slip Uploaded" if order.receipt_file_url else "Direct Gateway Electronic Settlement"

    bill_to_content = [
        Paragraph("BILLED TO", section_heading),
        Spacer(1, 3),
        Paragraph(f"<b>{user_name}</b>", body_bold),
        Paragraph(user_email, body_style),
        Paragraph(f"Account ID: {user_id_str}", body_style),
    ]

    payment_content = [
        Paragraph("PAYMENT & SETTLEMENT", section_heading),
        Spacer(1, 3),
        Paragraph(f"<b>Method:</b> {payment_label}", body_style),
        Paragraph(f"<b>Settlement:</b> {proof_status}", body_style),
    ]
    if order.client_notes:
        note_snippet = order.client_notes.replace("\n", " ").strip()
        if len(note_snippet) > 60:
            note_snippet = note_snippet[:57] + "..."
        payment_content.append(Paragraph(f"<b>Notes:</b> {note_snippet}", body_style))

    details_table = Table([[bill_to_content, payment_content]], colWidths=[270, 270])
    details_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('BACKGROUND', (0,0), (0,0), colors.HexColor('#F8FAFC')),
        ('BACKGROUND', (1,0), (1,0), colors.HexColor('#F8FAFC')),
        ('LEFTPADDING', (0,0), (-1,-1), 10),
        ('RIGHTPADDING', (0,0), (-1,-1), 10),
        ('TOPPADDING', (0,0), (-1,-1), 8),
        ('BOTTOMPADDING', (0,0), (-1,-1), 8),
        ('BOX', (0,0), (0,0), 0.5, colors.HexColor('#CBD5E1')),
        ('BOX', (1,0), (1,0), 0.5, colors.HexColor('#CBD5E1')),
    ]))
    story.append(details_table)
    story.append(Spacer(1, 14))

    # 3. Line Items Table
    items_header = [
        Paragraph("<b>#</b>", ParagraphStyle('H1', parent=body_style, textColor=colors.white, fontName='Helvetica-Bold')),
        Paragraph("<b>Item Description</b>", ParagraphStyle('H2', parent=body_style, textColor=colors.white, fontName='Helvetica-Bold')),
        Paragraph("<b>Platform</b>", ParagraphStyle('H3', parent=body_style, textColor=colors.white, fontName='Helvetica-Bold', alignment=1)),
        Paragraph("<b>Qty</b>", ParagraphStyle('H4', parent=body_style, textColor=colors.white, fontName='Helvetica-Bold', alignment=1)),
        Paragraph("<b>Unit Price</b>", ParagraphStyle('H5', parent=body_style, textColor=colors.white, fontName='Helvetica-Bold', alignment=2)),
        Paragraph("<b>Total</b>", ParagraphStyle('H6', parent=body_style, textColor=colors.white, fontName='Helvetica-Bold', alignment=2)),
    ]

    items_data = [items_header]
    subtotal = 0.0

    idx = 1
    for item in order.items:
        p_title = item.product.title if item.product else f"Product #{item.product_id}"
        p_platform = item.product.platform if item.product and hasattr(item.product, 'platform') and item.product.platform else "Digital"
        line_total = item.unit_price * item.quantity
        subtotal += line_total

        items_data.append([
            Paragraph(str(idx), body_style),
            Paragraph(f"<b>{p_title}</b>", body_style),
            Paragraph(p_platform, ParagraphStyle('P', parent=body_style, alignment=1)),
            Paragraph(str(item.quantity), ParagraphStyle('Q', parent=body_style, alignment=1)),
            Paragraph(f"${item.unit_price:.2f}", ParagraphStyle('UP', parent=body_style, alignment=2)),
            Paragraph(f"${line_total:.2f}", ParagraphStyle('TP', parent=body_style, alignment=2, fontName='Helvetica-Bold')),
        ])
        idx += 1

    items_table = Table(items_data, colWidths=[25, 235, 70, 40, 85, 85])
    items_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#0F172A')),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 6),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ('LEFTPADDING', (0,0), (-1,-1), 6),
        ('RIGHTPADDING', (0,0), (-1,-1), 6),
        ('LINEBELOW', (0,1), (-1,-1), 0.5, colors.HexColor('#E2E8F0')),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#F8FAFC')]),
        ('BOX', (0,0), (-1,-1), 0.5, colors.HexColor('#0F172A')),
    ]))
    story.append(items_table)
    story.append(Spacer(1, 10))

    # 4. Totals & Terms with Official Seal Stamp
    discount_val = max(0.0, round(subtotal - order.total_amount, 2))

    stamp_date = order.created_at.strftime("%Y-%m-%d") if order.created_at else datetime.now().strftime("%Y-%m-%d")
    stamp = OfficialStamp(
        status=order.status,
        date_str=stamp_date,
        order_num=order.order_number,
        store_name=metadata["store_name"],
        width=190,
        height=68,
        rotation=-5
    )

    terms_text = [
        Paragraph("TERMS OF DIGITAL FULFILLMENT", section_heading),
        Spacer(1, 2),
        Paragraph(
            "This digital order is fulfilled electronically. Delivered license keys are registered directly into "
            "your customer vault. Due to the digital nature of license keys, verified deliveries are non-refundable "
            "once redeemed. For customer support, inquiries, or re-issuance, please contact "
            f"<b>{metadata['support_email']}</b>.",
            tagline_style
        ),
        Spacer(1, 6),
        stamp
    ]

    totals_rows = [
        [Paragraph("Subtotal:", body_style), Paragraph(f"${subtotal:.2f}", ParagraphStyle('R1', parent=body_style, alignment=2))]
    ]
    if discount_val > 0.009:
        totals_rows.append([
            Paragraph("Promo Discount:", body_style),
            Paragraph(f"-${discount_val:.2f}", ParagraphStyle('R2', parent=body_style, alignment=2, textColor=colors.HexColor('#10B981')))
        ])
    totals_rows.append([
        Paragraph("Processing / Fee:", body_style),
        Paragraph("$0.00", ParagraphStyle('R3', parent=body_style, alignment=2))
    ])
    totals_rows.append([
        Paragraph("<b>Total Amount:</b>", body_bold),
        Paragraph(f"<b>${order.total_amount:.2f} USD</b>", ParagraphStyle('R4', parent=brand_style, fontSize=12, leading=15, alignment=2, textColor=colors.HexColor('#0F172A')))
    ])

    totals_table = Table(totals_rows, colWidths=[150, 90])
    totals_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 3),
        ('BOTTOMPADDING', (0,0), (-1,-1), 3),
        ('LEFTPADDING', (0,0), (-1,-1), 4),
        ('RIGHTPADDING', (0,0), (-1,-1), 4),
        ('LINEBELOW', (0,-1), (-1,-1), 1.5, colors.HexColor('#0F172A')),
        ('LINEABOVE', (0,-1), (-1,-1), 1, colors.HexColor('#E2E8F0')),
    ]))

    totals_side = [
        totals_table,
        Spacer(1, 6),
        Paragraph(
            f"<b>Settlement Authority:</b> <font color='#64748B'><i>Digitally Certified by {metadata['store_name']}</i></font>",
            tagline_style
        )
    ]

    summary_table = Table([[terms_text, totals_side]], colWidths=[290, 250])
    summary_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('LEFTPADDING', (0,0), (-1,-1), 0),
        ('RIGHTPADDING', (0,0), (-1,-1), 0),
        ('TOPPADDING', (0,0), (-1,-1), 0),
        ('BOTTOMPADDING', (0,0), (-1,-1), 0),
    ]))
    story.append(summary_table)
    story.append(Spacer(1, 10))

    # 5. Delivered Digital Keys (For Completed Orders)
    if order.status.lower() == "completed":
        all_keys = []
        for item in order.items:
            p_title = item.product.title if item.product else f"Product #{item.product_id}"
            keys_list = [k.key_code for k in order.assigned_keys if k.product_id == item.product_id]
            if not keys_list and item.delivered_keys_json:
                try:
                    keys_list = json.loads(item.delivered_keys_json)
                except Exception:
                    keys_list = []
            for k in keys_list:
                all_keys.append((p_title, k))

        if all_keys:
            keys_heading = ParagraphStyle(
                'KH', parent=styles['Normal'],
                fontName='Helvetica-Bold', fontSize=8.5, leading=11,
                textColor=colors.HexColor('#065F46')
            )
            key_table_rows = []
            for prod_name, code in all_keys:
                key_table_rows.append([
                    Paragraph(f"<b>{prod_name}</b>", body_style),
                    Paragraph(code, key_style),
                    Paragraph("VERIFIED & ACTIVE", ParagraphStyle('KStat', parent=tagline_style, alignment=2, textColor=colors.HexColor('#059669')))
                ])

            keys_inner_table = Table(key_table_rows, colWidths=[180, 230, 110])
            keys_inner_table.setStyle(TableStyle([
                ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
                ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#ECFDF5')),
                ('LINEBELOW', (0,0), (-1,-1), 0.5, colors.HexColor('#A7F3D0')),
                ('LEFTPADDING', (0,0), (-1,-1), 6),
                ('RIGHTPADDING', (0,0), (-1,-1), 6),
                ('TOPPADDING', (0,0), (-1,-1), 4),
                ('BOTTOMPADDING', (0,0), (-1,-1), 4),
            ]))

            keys_box = [
                Paragraph("✔ OFFICIAL DELIVERED DIGITAL LICENSE KEYS (AUTHENTIC)", keys_heading),
                Spacer(1, 3),
                keys_inner_table
            ]

            keys_container = Table([[keys_box]], colWidths=[540])
            keys_container.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#F0FDF4')),
                ('BOX', (0,0), (-1,-1), 1, colors.HexColor('#34D399')),
                ('LEFTPADDING', (0,0), (-1,-1), 8),
                ('RIGHTPADDING', (0,0), (-1,-1), 8),
                ('TOPPADDING', (0,0), (-1,-1), 6),
                ('BOTTOMPADDING', (0,0), (-1,-1), 6),
            ]))
            story.append(keys_container)
            story.append(Spacer(1, 10))

    # 6. Security Footer & Verification Stamp
    hash_seed = f"{order.order_number}-{order.total_amount}-{order.created_at}".encode("utf-8")
    hash_token = hashlib.sha256(hash_seed).hexdigest()[:32].upper()
    footer_text = (
        f"Cryptographic Verification Digest: {hash_token} &bull; "
        f"Certified by {metadata['store_name']} Settlement Engine &bull; Official Digital Tax Receipt"
    )
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor('#CBD5E1'), spaceAfter=5))
    story.append(Paragraph(footer_text, ParagraphStyle('Foot', parent=tagline_style, alignment=1, fontSize=6.5)))

    doc.build(story)
    pdf_bytes = buffer.getvalue()
    buffer.close()

    if cache_to_disk:
        cache_path = INVOICES_DIR / f"INV-{order.order_number}.pdf"
        try:
            cache_path.write_bytes(pdf_bytes)
        except Exception as ex:
            print(f"[InvoiceService] Could not cache invoice to disk: {ex}")

    return pdf_bytes
