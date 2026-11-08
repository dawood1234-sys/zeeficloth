import argparse
import os
import sqlite3
from getpass import getpass
from pathlib import Path
from werkzeug.security import generate_password_hash

import app as application


parser = argparse.ArgumentParser(description="Create or promote a Zeefi administrator")
parser.add_argument("email", nargs="?")
parser.add_argument("--password")
args = parser.parse_args()

base_dir = Path(__file__).resolve().parent
email = (args.email or os.environ.get("ADMIN_EMAIL") or input("Admin email: ")).strip().lower()
password = args.password or os.environ.get("ADMIN_PASSWORD") or getpass("Admin password: ")
if len(password) < 8:
    raise SystemExit("Password must be at least 8 characters.")

application.init_db()
db = sqlite3.connect(os.environ.get("DATABASE_PATH", application.DATABASE))
db.execute("INSERT OR IGNORE INTO roles(id,name) VALUES (1,'customer')")
db.execute("INSERT OR IGNORE INTO roles(id,name) VALUES (2,'admin')")
existing = db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
if existing:
    db.execute("UPDATE users SET role_id=2,password_hash=?,active=1 WHERE id=?",
               (generate_password_hash(password), existing[0]))
else:
    db.execute("INSERT INTO users(name,email,password_hash,role_id,active) VALUES(?,?,?,?,1)",
               ("Administrator", email, generate_password_hash(password), 2))
db.commit()
db.close()
print(f"Administrator access enabled for {email}")
