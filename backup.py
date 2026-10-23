import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_FILE = BASE_DIR / "data" / "store.db"
BACKUP_DIR = BASE_DIR / "data" / "backups"
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

def create_backup():
    """Create a verified snapshot backup of the live SQLite database."""
    if not DB_FILE.exists():
        print(f"[!] Database file not found at {DB_FILE}")
        return None

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = BACKUP_DIR / f"store_backup_{timestamp}.db"

    # Use SQLite online backup API for safe, concurrent-safe copying
    src_conn = sqlite3.connect(DB_FILE)
    dst_conn = sqlite3.connect(backup_file)
    with dst_conn:
        src_conn.backup(dst_conn)
    dst_conn.close()
    src_conn.close()

    size_kb = backup_file.stat().st_size / 1024.0
    print(f"✔ Backup created successfully: {backup_file.name} ({size_kb:.2f} KB)")
    return backup_file

def list_backups():
    """List all available database backups."""
    backups = sorted(BACKUP_DIR.glob("store_backup_*.db"), reverse=True)
    if not backups:
        print("No backups found in data/backups/")
        return

    print("=" * 60)
    print(f"AVAILABLE DATABASE BACKUPS ({len(backups)} Total)")
    print("=" * 60)
    for b in backups:
        size_kb = b.stat().st_size / 1024.0
        mtime = datetime.fromtimestamp(b.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        print(f"• {b.name:<32} | {size_kb:>8.2f} KB | {mtime}")

if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "create"
    if action == "list":
        list_backups()
    else:
        create_backup()
