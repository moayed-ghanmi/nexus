"""
NEXUS KEYS - Customer Support & Order Inquiries Controller
Handles client ticket submissions, real-time threaded replies, and order linkings.
"""

import time
import random
from datetime import datetime, timezone
from pathlib import Path
from fastapi import APIRouter, Request, Depends, HTTPException, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import (
    User, Order, Ticket, TicketMessage, TicketStatus, TicketPriority
)
from app.security import get_current_user_from_request

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))

@router.get("/support", response_class=HTMLResponse)
async def support_portal(
    request: Request,
    order: str = None,
    db: Session = Depends(get_db)
):
    """Render customer support hub with active inquiries, order linking, and FAQ."""
    current_user = get_current_user_from_request(request, db)
    if not current_user:
        next_url = f"/support?order={order}" if order else "/support"
        return RedirectResponse(url=f"/auth/login?next={next_url}", status_code=303)

    user_tickets = db.query(Ticket).filter(
        Ticket.user_id == current_user.id
    ).order_by(Ticket.updated_at.desc()).all()

    user_orders = db.query(Order).filter(
        Order.user_id == current_user.id
    ).order_by(Order.id.desc()).limit(15).all()

    prefill_order = order if order else ""

    return templates.TemplateResponse(
        request=request,
        name="store/support.html",
        context={
            "current_user": current_user,
            "tickets": user_tickets,
            "orders": user_orders,
            "prefill_order": prefill_order,
            "success": request.query_params.get("success"),
            "error": request.query_params.get("error")
        }
    )

@router.post("/support/create")
async def create_support_ticket(
    request: Request,
    subject: str = Form(...),
    category: str = Form("general"),
    priority: str = Form("normal"),
    order_number: str = Form(""),
    message: str = Form(...),
    db: Session = Depends(get_db)
):
    """Customer creates a new support inquiry or order dispute."""
    current_user = get_current_user_from_request(request, db)
    if not current_user:
        return RedirectResponse(url="/auth/login", status_code=303)

    clean_subject = subject.strip()
    clean_message = message.strip()

    if not clean_subject or not clean_message:
        return RedirectResponse(url="/support?error=Subject+and+message+cannot+be+empty", status_code=303)

    # Link order if provided and owned
    linked_order = None
    clean_order_num = order_number.strip()
    if clean_order_num:
        linked_order = db.query(Order).filter(
            Order.order_number == clean_order_num,
            Order.user_id == current_user.id
        ).first()

    # Generate unique ticket number
    ticket_num = f"TCK-{int(time.time())}-{random.randint(100, 999)}"

    ticket = Ticket(
        ticket_number=ticket_num,
        user_id=current_user.id,
        order_id=linked_order.id if linked_order else None,
        subject=clean_subject,
        category=category.strip() if category else "general",
        priority=priority.strip() if priority in ["low", "normal", "high", "urgent"] else "normal",
        status=TicketStatus.OPEN.value,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc)
    )
    db.add(ticket)
    db.flush()

    # First message in thread
    initial_msg = TicketMessage(
        ticket_id=ticket.id,
        sender_id=current_user.id,
        sender_role="customer",
        message=clean_message,
        created_at=datetime.now(timezone.utc)
    )
    db.add(initial_msg)
    db.commit()

    return RedirectResponse(
        url=f"/support/ticket/{ticket.ticket_number}?success=Support+ticket+created+successfully.+Our+operations+team+will+review+it+shortly.",
        status_code=303
    )

@router.get("/support/ticket/{ticket_number}", response_class=HTMLResponse)
async def view_ticket_thread(
    ticket_number: str,
    request: Request,
    db: Session = Depends(get_db)
):
    """Customer views specific support thread, past replies, and linked order data."""
    current_user = get_current_user_from_request(request, db)
    if not current_user:
        return RedirectResponse(url=f"/auth/login?next=/support/ticket/{ticket_number}", status_code=303)

    ticket = db.query(Ticket).filter(Ticket.ticket_number == ticket_number).first()
    if not ticket or (ticket.user_id != current_user.id and not current_user.is_admin_user):
        raise HTTPException(status_code=404, detail="Support ticket not found or unauthorized.")

    return templates.TemplateResponse(
        request=request,
        name="store/ticket_detail.html",
        context={
            "current_user": current_user,
            "ticket": ticket,
            "success": request.query_params.get("success"),
            "error": request.query_params.get("error")
        }
    )

@router.post("/support/ticket/{ticket_number}/reply")
async def client_reply_to_ticket(
    ticket_number: str,
    request: Request,
    message: str = Form(...),
    db: Session = Depends(get_db)
):
    """Customer submits follow-up reply in an open ticket thread."""
    current_user = get_current_user_from_request(request, db)
    if not current_user:
        return RedirectResponse(url="/auth/login", status_code=303)

    ticket = db.query(Ticket).filter(Ticket.ticket_number == ticket_number).first()
    if not ticket or (ticket.user_id != current_user.id and not current_user.is_admin_user):
        raise HTTPException(status_code=404, detail="Support ticket not found or unauthorized.")

    clean_message = message.strip()
    if not clean_message:
        return RedirectResponse(url=f"/support/ticket/{ticket_number}?error=Reply+message+cannot+be+empty", status_code=303)

    reply_msg = TicketMessage(
        ticket_id=ticket.id,
        sender_id=current_user.id,
        sender_role="customer",
        message=clean_message,
        created_at=datetime.now(timezone.utc)
    )
    db.add(reply_msg)

    # Reopen status if waiting on customer or resolved
    if ticket.status in [TicketStatus.WAITING_CLIENT.value, TicketStatus.RESOLVED.value, TicketStatus.CLOSED.value]:
        ticket.status = TicketStatus.OPEN.value

    ticket.updated_at = datetime.now(timezone.utc)
    db.commit()

    return RedirectResponse(
        url=f"/support/ticket/{ticket_number}?success=Your+reply+has+been+posted.",
        status_code=303
    )
