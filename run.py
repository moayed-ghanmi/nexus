import os
import sys
import uvicorn
from app.seed_data import seed_database

if __name__ == "__main__":
    print("=" * 60)
    print("NEXUS KEYS - Digital Products & Gaming Services Platform")
    print("=" * 60)
    print("Initializing Database & Seeding Products/Keys...")
    seed_database()
    print("Database Ready.")

    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))

    print(f"\n[+] Public Storefront : http://{host}:{port}")
    print(f"[+] Client Login      : http://{host}:{port}/auth/login")
    print(f"[+] Hidden Admin Gate : http://{host}:{port}/portal-vault-admin")
    print("=" * 60)
    print(f"Starting Server on {host}:{port} ... (Press CTRL+C to quit)\n")
    uvicorn.run("app.main:app", host=host, port=port, reload=False)
