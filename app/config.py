import os
import shutil
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Vercel Serverless Compatibility:
# On Vercel, the source directory is read-only. We use /tmp/nexus_data for database and runtime writes.
if os.getenv("VERCEL"):
    DATA_DIR = Path("/tmp/nexus_data")
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    # Copy seeded database to /tmp if not already initialized
    src_db = BASE_DIR / "data" / "store.db"
    dest_db = DATA_DIR / "store.db"
    if src_db.exists() and not dest_db.exists():
        try:
            shutil.copy2(src_db, dest_db)
        except Exception as e:
            print(f"[Config] Vercel DB copy notice: {e}")
    DEFAULT_DB_URL = f"sqlite:///{dest_db}"
else:
    DATA_DIR = BASE_DIR / "data"
    try:
        DATA_DIR.mkdir(exist_ok=True)
    except Exception:
        pass
    DEFAULT_DB_URL = f"sqlite:///{DATA_DIR / 'store.db'}"

# Database URL (supports remote Postgres/MySQL via DATABASE_URL or local SQLite)
DATABASE_URL = os.getenv("DATABASE_URL", DEFAULT_DB_URL)

# Lightweight .env loader (zero external dependencies)
env_file = BASE_DIR / ".env"
if env_file.exists():
    try:
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip("'\""))
    except Exception as e:
        print(f"[Config] Notice: Could not read .env: {e}")

# Core Security & App Settings
SECRET_KEY = os.getenv("SECRET_KEY", "super-secret-digital-vault-key-2026-production-ready")
SESSION_COOKIE_NAME = "nexus_session"

# Hidden Admin Gateway Route (Customizable, completely unlinked in client UI)
ADMIN_SECRET_URL = os.getenv("ADMIN_SECRET_URL", "/portal-vault-admin")

# Database URL
DATABASE_URL = f"sqlite:///{DATA_DIR / 'store.db'}"

# Application Info
APP_NAME = "NEXUS KEYS"
APP_TAGLINE = "Instant Digital Keys, Gaming Cards & Software Subscriptions"
