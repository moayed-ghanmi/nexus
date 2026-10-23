from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from pathlib import Path
from app.config import APP_NAME
from app.routes import store, auth, admin, cart, checkout, support, wallet, wishlist
from app.seed_data import seed_database

app = FastAPI(
    title=APP_NAME,
    description="Digital Products, Gaming Cards & Subscriptions E-commerce Engine",
    version="1.0.0"
)

# Initialize and seed database on launch
@app.on_event("startup")
def on_startup():
    seed_database()

# Mount Static & Uploads Directory
BASE_DIR = Path(__file__).resolve().parent.parent
static_dir = BASE_DIR / "static"
try:
    static_dir.mkdir(exist_ok=True)
    uploads_dir = static_dir / "uploads" / "receipts"
    uploads_dir.mkdir(parents=True, exist_ok=True)
except Exception:
    pass
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# Register Routers
app.include_router(store.router)
app.include_router(cart.router)
app.include_router(checkout.router)
app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(support.router)
app.include_router(wallet.router)
app.include_router(wishlist.router)

from fastapi.templating import Jinja2Templates
from fastapi.exceptions import HTTPException
error_templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))

@app.exception_handler(404)
async def custom_404_handler(request: Request, exc: HTTPException):
    return error_templates.TemplateResponse(
        request=request,
        name="errors/404.html",
        context={"current_user": None},
        status_code=404
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
