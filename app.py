import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

from flask import Flask, g, jsonify, request, send_from_directory


BASE_DIR = Path(__file__).resolve().parent
env_file = BASE_DIR / ".env"
if env_file.exists():
    for env_line in env_file.read_text(encoding="utf-8").splitlines():
        line = env_line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))
DATABASE = os.environ.get("DATABASE_PATH", str(BASE_DIR / "zeefi.db"))
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

app = Flask(__name__, static_folder=str(BASE_DIR), static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024
JWT_SECRET = os.environ.get("JWT_SECRET", "change-this-secret-in-production")

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS roles (id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL);
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL, role_id INTEGER NOT NULL REFERENCES roles(id),
  phone TEXT, active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS categories (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL, slug TEXT UNIQUE NOT NULL,
  description TEXT, image TEXT, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS products (
  id INTEGER PRIMARY KEY AUTOINCREMENT, category_id INTEGER REFERENCES categories(id),
  name TEXT NOT NULL, slug TEXT UNIQUE NOT NULL, description TEXT NOT NULL DEFAULT '',
  price REAL NOT NULL CHECK(price >= 0), discount_price REAL CHECK(discount_price IS NULL OR discount_price >= 0),
  stock INTEGER NOT NULL DEFAULT 0 CHECK(stock >= 0), featured INTEGER NOT NULL DEFAULT 0,
  trending INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS product_images (
  id INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
  url TEXT NOT NULL, sort_order INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS product_sizes (
  id INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
  name TEXT NOT NULL, UNIQUE(product_id, name)
);
CREATE TABLE IF NOT EXISTS product_colors (
  id INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
  name TEXT NOT NULL, hex TEXT, UNIQUE(product_id, name)
);
CREATE TABLE IF NOT EXISTS carts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
  quantity INTEGER NOT NULL DEFAULT 1 CHECK(quantity > 0), size TEXT, color TEXT,
  UNIQUE(user_id, product_id, size, color)
);
CREATE TABLE IF NOT EXISTS wishlists (
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(user_id, product_id)
);
CREATE TABLE IF NOT EXISTS orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT, order_number TEXT UNIQUE NOT NULL, user_id INTEGER REFERENCES users(id),
  status TEXT NOT NULL DEFAULT 'Pending', subtotal REAL NOT NULL, shipping REAL NOT NULL DEFAULT 0,
  discount REAL NOT NULL DEFAULT 0, total REAL NOT NULL, payment_method TEXT NOT NULL DEFAULT 'Cash on Delivery',
  payment_status TEXT NOT NULL DEFAULT 'Pending', notes TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CHECK(status IN ('Pending','Confirmed','Processing','Shipped','Delivered','Cancelled'))
);
CREATE TABLE IF NOT EXISTS order_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
  product_id INTEGER REFERENCES products(id) ON DELETE SET NULL,
  name TEXT NOT NULL, unit_price REAL NOT NULL, quantity INTEGER NOT NULL CHECK(quantity > 0),
  size TEXT, color TEXT
);
CREATE TABLE IF NOT EXISTS shipping_addresses (
  id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER UNIQUE NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
  full_name TEXT NOT NULL, phone TEXT NOT NULL, address TEXT NOT NULL, city TEXT NOT NULL,
  province TEXT, postal_code TEXT, country TEXT NOT NULL DEFAULT 'Pakistan'
);
CREATE TABLE IF NOT EXISTS reviews (
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE, order_id INTEGER REFERENCES orders(id),
  rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5), title TEXT, comment TEXT,
  status TEXT NOT NULL DEFAULT 'Pending' CHECK(status IN ('Pending','Approved','Rejected')),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(user_id, product_id, order_id)
);
CREATE TABLE IF NOT EXISTS payments (
  id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
  method TEXT NOT NULL, amount REAL NOT NULL, status TEXT NOT NULL DEFAULT 'Pending',
  transaction_id TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS contact_messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, email TEXT NOT NULL, message TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'Unread', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS coupons (
  id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE NOT NULL, type TEXT NOT NULL DEFAULT 'percentage',
  value REAL NOT NULL, minimum_amount REAL NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1,
  expires_at TEXT
);
CREATE TABLE IF NOT EXISTS banners (
  id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, subtitle TEXT, image TEXT, link TEXT,
  active INTEGER NOT NULL DEFAULT 1, sort_order INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY, value TEXT NOT NULL
);
"""


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DATABASE)
    db.executescript(SCHEMA)
    db.execute("INSERT OR IGNORE INTO roles(id,name) VALUES (1,'customer')")
    db.execute("INSERT OR IGNORE INTO roles(id,name) VALUES (2,'admin')")
    defaults = {
        "store_name": "Zeefi Clothing",
        "store_email": os.environ.get("STORE_EMAIL", ""),
        "currency": "PKR",
        "shipping_charges": os.environ.get("SHIPPING_CHARGES", "250"),
        "free_shipping_minimum": "5000",
        "payment_methods": "Cash on Delivery, Bank Transfer",
    }
    for key, value in defaults.items():
        db.execute("INSERT OR IGNORE INTO settings(key,value) VALUES (?,?)", (key, value))
    admin_email = os.environ.get("ADMIN_EMAIL")
    admin_password = os.environ.get("ADMIN_PASSWORD")
    if admin_email and admin_password:
        existing = db.execute("SELECT id FROM users WHERE email=?", (admin_email.lower(),)).fetchone()
        if not existing:
            db.execute(
                "INSERT INTO users(name,email,password_hash,role_id) VALUES(?,?,?,2)",
                ("Administrator", admin_email.lower(), generate_password_hash(admin_password)),
            )
        else:
            db.execute(
                "UPDATE users SET role_id=2,password_hash=?,active=1 WHERE id=?",
                (generate_password_hash(admin_password), existing[0]),
            )
    db.commit()
    db.close()


def rows(query, params=()):
    return [dict(r) for r in get_db().execute(query, params).fetchall()]


def row(query, params=()):
    result = get_db().execute(query, params).fetchone()
    return dict(result) if result else None


def slug(value):
    return "".join(c.lower() if c.isalnum() else "-" for c in value).strip("-").replace("--", "-")


def token_encode(user_id):
    payload = base64.urlsafe_b64encode(json.dumps({
        "sub": user_id, "exp": int(time.time()) + 7 * 24 * 3600, "nonce": secrets.token_hex(6)
    }, separators=(",", ":")).encode()).rstrip(b"=").decode()
    signature = hmac.new(JWT_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def token_decode(token):
    try:
        payload, signature = token.rsplit(".", 1)
        expected = hmac.new(JWT_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        padded = payload + "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded))
        if data["exp"] < time.time():
            return None
        return data
    except (ValueError, KeyError, json.JSONDecodeError, base64.binascii.Error):
        return None


def current_user(required=False):
    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer "):
        if required:
            return None
        return None
    payload = token_decode(authorization[7:])
    if not payload:
        return None
    return row("""SELECT users.*, roles.name AS role FROM users
                  JOIN roles ON roles.id=users.role_id WHERE users.id=?""", (payload["sub"],))


def auth(required_role=None):
    def decorator(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            user = current_user(True)
            if not user:
                return jsonify(error="Authentication required"), 401
            if not user["active"]:
                return jsonify(error="Account is inactive"), 403
            if required_role and user["role"] != required_role:
                return jsonify(error="Administrator access required"), 403
            g.user = user
            return fn(*args, **kwargs)
        return wrapped
    return decorator


def validate(payload, fields):
    if not isinstance(payload, dict):
        return "JSON body is required"
    for field, required_type in fields.items():
        value = payload.get(field)
        if value is None:
            return f"{field} is required"
        if isinstance(required_type, tuple):
            if type(value) not in required_type:
                return f"{field} has an invalid type"
        elif isinstance(value, required_type) is False:
            return f"{field} has an invalid type"
    return None


@app.after_request
def security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    return response


@app.route("/")
def index():
    return send_from_directory(BASE_DIR, "index.html")


@app.post("/api/auth/register")
def register():
    data = request.get_json(silent=True) or {}
    error = validate(data, {"name": str, "email": str, "password": str})
    if error:
        return jsonify(error=error), 400
    if len(data["password"]) < 8:
        return jsonify(error="Password must be at least 8 characters"), 400
    email = data["email"].strip().lower()
    db = get_db()
    if row("SELECT id FROM users WHERE email=?", (email,)):
        return jsonify(error="An account with this email already exists"), 409
    cursor = db.execute(
        "INSERT INTO users(name,email,password_hash,role_id,phone) VALUES(?,?,?,?,?)",
        (data["name"].strip(), email, generate_password_hash(data["password"]), 1, data.get("phone")),
    )
    db.commit()
    user = row("SELECT users.*,roles.name role FROM users JOIN roles ON roles.id=users.role_id WHERE users.id=?",
               (cursor.lastrowid,))
    return jsonify(token=token_encode(user["id"]), user=user_public(user)), 201


@app.post("/api/auth/login")
def login():
    data = request.get_json(silent=True) or {}
    error = validate(data, {"email": str, "password": str})
    if error:
        return jsonify(error=error), 400
    user = row("""SELECT users.*,roles.name role FROM users JOIN roles ON roles.id=users.role_id WHERE email=?""",
               (data["email"].strip().lower(),))
    if not user or not check_password_hash(user["password_hash"], data["password"]):
        return jsonify(error="Invalid email or password"), 401
    if not user["active"]:
        return jsonify(error="Account is inactive"), 403
    return jsonify(token=token_encode(user["id"]), user=user_public(user))


@app.get("/api/auth/me")
@auth()
def me():
    return jsonify(user=user_public(g.user))


def user_public(user):
    return {key: user[key] for key in ("id", "name", "email", "phone", "role", "active", "created_at") if key in user}


@app.route("/api/categories", methods=["GET"])
def public_categories():
    return jsonify(categories=rows("SELECT * FROM categories WHERE active=1 ORDER BY name"))


@app.post("/api/categories")
@auth("admin")
def create_category():
    data = request.get_json(silent=True) or {}
    if not data.get("name"):
        return jsonify(error="Name is required"), 400
    try:
        db = get_db()
        cursor = db.execute("INSERT INTO categories(name,slug,description,image,active) VALUES(?,?,?,?,?)",
            (data["name"].strip(), slug(data["name"]), data.get("description"), data.get("image"), int(data.get("active", True))))
        db.commit()
        return jsonify(category=row("SELECT * FROM categories WHERE id=?", (cursor.lastrowid,))), 201
    except sqlite3.IntegrityError:
        return jsonify(error="Category already exists"), 409


@app.put("/api/categories/<int:category_id>")
@auth("admin")
def update_category(category_id):
    data = request.get_json(silent=True) or {}
    db = get_db()
    if not row("SELECT id FROM categories WHERE id=?", (category_id,)):
        return jsonify(error="Category not found"), 404
    db.execute("""UPDATE categories SET name=COALESCE(?,name), slug=COALESCE(?,slug),
                  description=COALESCE(?,description), image=COALESCE(?,image),
                  active=COALESCE(?,active) WHERE id=?""",
               (data.get("name"), data.get("slug") or (slug(data["name"]) if data.get("name") else None),
                data.get("description"), data.get("image"), data.get("active"), category_id))
    db.commit()
    return jsonify(category=row("SELECT * FROM categories WHERE id=?", (category_id,)))


@app.delete("/api/categories/<int:category_id>")
@auth("admin")
def delete_category(category_id):
    db = get_db()
    if row("SELECT id FROM products WHERE category_id=? LIMIT 1", (category_id,)):
        return jsonify(error="Move or delete products in this category first"), 409
    if db.execute("DELETE FROM categories WHERE id=?", (category_id,)).rowcount == 0:
        return jsonify(error="Category not found"), 404
    db.commit()
    return "", 204


def product_json(product_id):
    product = row("""SELECT p.*, c.name AS category_name FROM products p
                     LEFT JOIN categories c ON c.id=p.category_id WHERE p.id=?""", (product_id,))
    if not product:
        return None
    product["images"] = rows("SELECT id,url FROM product_images WHERE product_id=? ORDER BY sort_order,id", (product_id,))
    product["sizes"] = rows("SELECT name FROM product_sizes WHERE product_id=? ORDER BY id", (product_id,))
    product["colors"] = rows("SELECT name,hex FROM product_colors WHERE product_id=? ORDER BY id", (product_id,))
    ratings = row("SELECT COUNT(*) count, ROUND(AVG(rating),1) average FROM reviews WHERE product_id=? AND status='Approved'", (product_id,))
    product["rating_count"] = ratings["count"]
    product["average_rating"] = ratings["average"] or 0
    return product


@app.get("/api/products")
def products():
    conditions = ["p.active=1"]
    params = []
    if request.args.get("category"):
        conditions.append("(c.slug=? OR c.id=?)")
        params.extend([request.args["category"], request.args["category"]])
    if request.args.get("search"):
        conditions.append("(p.name LIKE ? OR p.description LIKE ?)")
        params.extend([f"%{request.args['search']}%", f"%{request.args['search']}%"])
    low = request.args.get("min")
    high = request.args.get("max")
    if low:
        conditions.append("COALESCE(p.discount_price,p.price)>=?")
        params.append(float(low))
    if high:
        conditions.append("COALESCE(p.discount_price,p.price)<=?")
        params.append(float(high))
    if request.args.get("featured") == "true":
        conditions.append("p.featured=1")
    if request.args.get("trending") == "true":
        conditions.append("p.trending=1")
    size = request.args.get("size")
    if size:
        conditions.append("EXISTS (SELECT 1 FROM product_sizes s WHERE s.product_id=p.id AND s.name=?)")
        params.append(size)
    query = f"""SELECT p.*,c.name category_name FROM products p
                LEFT JOIN categories c ON c.id=p.category_id
                WHERE {' AND '.join(conditions)}
                ORDER BY p.featured DESC,p.created_at DESC"""
    output = []
    for product in rows(query, params):
        output.append(product_json(product["id"]))
    return jsonify(products=output)


@app.get("/api/products/<int:product_id>")
def product_detail(product_id):
    product = product_json(product_id)
    if not product or not product["active"]:
        return jsonify(error="Product not found"), 404
    reviews = rows("""SELECT r.id,r.rating,r.title,r.comment,r.created_at,u.name
                      FROM reviews r JOIN users u ON u.id=r.user_id
                      WHERE r.product_id=? AND r.status='Approved' ORDER BY r.created_at DESC""", (product_id,))
    return jsonify(product=product, reviews=reviews)


@app.post("/api/products")
@auth("admin")
def create_product():
    data = request.get_json(silent=True) or {}
    error = validate(data, {"name": str, "price": (int, float), "category_id": (int,)})
    if error:
        return jsonify(error=error), 400
    if data.get("category_id") and not row("SELECT id FROM categories WHERE id=?", (data["category_id"],)):
        return jsonify(error="Category not found"), 400
    db = get_db()
    product_slug = slug(data["name"])
    if row("SELECT id FROM products WHERE slug=?", (product_slug,)):
        product_slug = f"{product_slug}-{uuid.uuid4().hex[:6]}"
    try:
        cursor = db.execute("""INSERT INTO products(category_id,name,slug,description,price,discount_price,stock,featured,trending,active)
                               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (data["category_id"], data["name"].strip(), product_slug, data.get("description", ""),
             data["price"], data.get("discount_price"), data.get("stock", 0),
             int(bool(data.get("featured"))), int(bool(data.get("trending"))), int(data.get("active", True))))
        product_id = cursor.lastrowid
        save_product_options(db, product_id, data)
        db.commit()
        return jsonify(product=product_json(product_id)), 201
    except sqlite3.IntegrityError as exc:
        return jsonify(error=str(exc)), 400


def save_product_options(db, product_id, data):
    db.execute("DELETE FROM product_sizes WHERE product_id=?", (product_id,))
    db.execute("DELETE FROM product_colors WHERE product_id=?", (product_id,))
    db.execute("DELETE FROM product_images WHERE product_id=?", (product_id,))
    for image in data.get("images", []):
        url = image.get("url") if isinstance(image, dict) else image
        if url:
            db.execute("INSERT INTO product_images(product_id,url) VALUES(?,?)", (product_id, url))
    for size in data.get("sizes", []):
        name = size.get("name") if isinstance(size, dict) else size
        if name:
            db.execute("INSERT OR IGNORE INTO product_sizes(product_id,name) VALUES(?,?)", (product_id, name))
    for color in data.get("colors", []):
        if isinstance(color, dict):
            db.execute("INSERT OR IGNORE INTO product_colors(product_id,name,hex) VALUES(?,?,?)",
                       (product_id, color["name"], color.get("hex")))
        elif color:
            db.execute("INSERT OR IGNORE INTO product_colors(product_id,name) VALUES(?,?)", (product_id, color))


@app.put("/api/products/<int:product_id>")
@auth("admin")
def update_product(product_id):
    data = request.get_json(silent=True) or {}
    if not row("SELECT id FROM products WHERE id=?", (product_id,)):
        return jsonify(error="Product not found"), 404
    db = get_db()
    fields = ("category_id", "name", "description", "price", "discount_price", "stock", "featured", "trending", "active")
    updates = []
    values = []
    for field in fields:
        if field in data:
            updates.append(f"{field}=?")
            values.append(int(data[field]) if field in ("featured", "trending", "active") else data[field])
    updates.append("updated_at=CURRENT_TIMESTAMP")
    db.execute(f"UPDATE products SET {','.join(updates)} WHERE id=?", (*values, product_id))
    save_product_options(db, product_id, data) if any(k in data for k in ("images", "sizes", "colors")) else None
    db.commit()
    return jsonify(product=product_json(product_id))


@app.delete("/api/products/<int:product_id>")
@auth("admin")
def delete_product(product_id):
    if row("SELECT id FROM order_items WHERE product_id=? LIMIT 1", (product_id,)):
        return jsonify(error="Products with order history must be archived instead of deleted"), 409
    db = get_db()
    if db.execute("DELETE FROM products WHERE id=?", (product_id,)).rowcount == 0:
        return jsonify(error="Product not found"), 404
    db.commit()
    return "", 204


@app.post("/api/uploads/image")
@auth("admin")
def upload_image():
    file = request.files.get("image")
    if not file or file.filename == "":
        return jsonify(error="Image is required"), 400
    extension = (file.filename.rsplit(".", 1)[-1] or "").lower()
    if extension not in {"jpg", "jpeg", "png", "webp"}:
        return jsonify(error="Only JPG, PNG, and WebP images are allowed"), 400
    filename = f"{uuid.uuid4().hex}.{extension}"
    file.save(UPLOAD_DIR / filename)
    return jsonify(url=f"/uploads/{filename}"), 201


@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    return send_from_directory(UPLOAD_DIR, filename)


def cart_product(cart):
    product = product_json(cart["product_id"])
    if not product:
        return None
    return {**cart, "product": product,
            "price": product["discount_price"] if product["discount_price"] is not None else product["price"]}


@app.get("/api/cart")
@auth()
def get_cart():
    items = rows("SELECT * FROM carts WHERE user_id=? ORDER BY id", (g.user["id"],))
    return jsonify(items=[cart_product(item) for item in items if cart_product(item)])


@app.post("/api/cart")
@auth()
def add_cart():
    data = request.get_json(silent=True) or {}
    error = validate(data, {"product_id": (int,), "quantity": (int,)})
    if error:
        return jsonify(error=error), 400
    product = row("SELECT * FROM products WHERE id=? AND active=1", (data["product_id"],))
    if not product:
        return jsonify(error="Product not found"), 404
    quantity = data["quantity"]
    if quantity < 1 or quantity > product["stock"]:
        return jsonify(error="Requested quantity is not available"), 400
    db = get_db()
    db.execute("""INSERT INTO carts(user_id,product_id,quantity,size,color) VALUES(?,?,?,?,?)
                  ON CONFLICT(user_id,product_id,size,color) DO UPDATE SET quantity=quantity+excluded.quantity""",
               (g.user["id"], data["product_id"], quantity, data.get("size"), data.get("color")))
    db.commit()
    return jsonify(message="Added to cart"), 201


@app.put("/api/cart/<int:cart_id>")
@auth()
def update_cart(cart_id):
    data = request.get_json(silent=True) or {}
    item = row("SELECT * FROM carts WHERE id=? AND user_id=?", (cart_id, g.user["id"]))
    if not item:
        return jsonify(error="Cart item not found"), 404
    quantity = int(data.get("quantity", 0))
    if quantity < 1:
        return jsonify(error="Quantity must be at least 1"), 400
    stock = row("SELECT stock FROM products WHERE id=?", (item["product_id"],))["stock"]
    if quantity > stock:
        return jsonify(error="Requested quantity is not available"), 400
    get_db().execute("UPDATE carts SET quantity=? WHERE id=?", (quantity, cart_id))
    get_db().commit()
    return jsonify(message="Cart updated")


@app.delete("/api/cart/<int:cart_id>")
@auth()
def remove_cart(cart_id):
    get_db().execute("DELETE FROM carts WHERE id=? AND user_id=?", (cart_id, g.user["id"]))
    get_db().commit()
    return "", 204


@app.get("/api/wishlist")
@auth()
def get_wishlist():
    items = rows("""SELECT p.id FROM wishlists w JOIN products p ON p.id=w.product_id
                    WHERE w.user_id=? AND p.active=1""", (g.user["id"],))
    return jsonify(products=[product_json(item["id"]) for item in items])


@app.post("/api/wishlist/<int:product_id>")
@auth()
def add_wishlist(product_id):
    if not row("SELECT id FROM products WHERE id=? AND active=1", (product_id,)):
        return jsonify(error="Product not found"), 404
    get_db().execute("INSERT OR IGNORE INTO wishlists(user_id,product_id) VALUES(?,?)", (g.user["id"], product_id))
    get_db().commit()
    return jsonify(message="Added to wishlist"), 201


@app.delete("/api/wishlist/<int:product_id>")
@auth()
def remove_wishlist(product_id):
    get_db().execute("DELETE FROM wishlists WHERE user_id=? AND product_id=?", (g.user["id"], product_id))
    get_db().commit()
    return "", 204


@app.post("/api/checkout")
@auth()
def checkout():
    data = request.get_json(silent=True) or {}
    for field in ("full_name", "phone", "address", "city"):
        if not data.get(field):
            return jsonify(error=f"{field} is required"), 400
    cart = rows("SELECT * FROM carts WHERE user_id=?", (g.user["id"],))
    if not cart:
        return jsonify(error="Cart is empty"), 400
    db = get_db()
    subtotal = 0
    for item in cart:
        product = row("SELECT * FROM products WHERE id=? AND active=1", (item["product_id"],))
        if not product or item["quantity"] > product["stock"]:
            return jsonify(error=f"Stock changed for product {item['product_id']}"), 409
        subtotal += (product["discount_price"] if product["discount_price"] is not None else product["price"]) * item["quantity"]
    shipping_setting = float(row("SELECT value FROM settings WHERE key='shipping_charges'")["value"])
    shipping = 0 if subtotal >= 5000 else shipping_setting
    discount = 0
    coupon_code = (data.get("coupon_code") or "").strip().upper()
    if coupon_code:
        coupon = row("SELECT * FROM coupons WHERE code=? AND active=1 AND (expires_at IS NULL OR expires_at>CURRENT_TIMESTAMP)",
                     (coupon_code,))
        if not coupon or subtotal < coupon["minimum_amount"]:
            return jsonify(error="Invalid or unmet coupon"), 400
        discount = subtotal * coupon["value"] / 100 if coupon["type"] == "percentage" else coupon["value"]
        discount = min(discount, subtotal)
    total = round(subtotal + shipping - discount, 2)
    order_number = f"ZFC-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{secrets.token_hex(3).upper()}"
    try:
        db.execute("BEGIN")
        cursor = db.execute("""INSERT INTO orders(order_number,user_id,subtotal,shipping,discount,total,payment_method,notes)
                               VALUES(?,?,?,?,?,?,?,?)""",
                            (order_number, g.user["id"], subtotal, shipping, discount, total,
                             data.get("payment_method", "Cash on Delivery"), data.get("notes")))
        order_id = cursor.lastrowid
        for item in cart:
            product = row("SELECT * FROM products WHERE id=?", (item["product_id"],))
            unit = product["discount_price"] if product["discount_price"] is not None else product["price"]
            db.execute("""INSERT INTO order_items(order_id,product_id,name,unit_price,quantity,size,color)
                          VALUES(?,?,?,?,?,?,?)""",
                       (order_id, product["id"], product["name"], unit, item["quantity"], item["size"], item["color"]))
            db.execute("UPDATE products SET stock=stock-? WHERE id=?", (item["quantity"], product["id"]))
        db.execute("""INSERT INTO shipping_addresses(order_id,full_name,phone,address,city,province,postal_code,country)
                      VALUES(?,?,?,?,?,?,?,?)""",
                   (order_id, data["full_name"], data["phone"], data["address"], data["city"],
                    data.get("province"), data.get("postal_code"), data.get("country", "Pakistan")))
        db.execute("INSERT INTO payments(order_id,method,amount,status) VALUES(?,?,?,?)",
                   (order_id, data.get("payment_method", "Cash on Delivery"), total,
                    "Pending" if data.get("payment_method", "Cash on Delivery") != "Online" else "Pending"))
        db.execute("DELETE FROM carts WHERE user_id=?", (g.user["id"],))
        db.commit()
        return jsonify(order=order_detail(order_id)), 201
    except Exception:
        db.rollback()
        raise


def order_detail(order_id):
    order = row("""SELECT o.*,u.name customer_name,u.email customer_email FROM orders o
                   LEFT JOIN users u ON u.id=o.user_id WHERE o.id=?""", (order_id,))
    if not order:
        return None
    order["items"] = rows("SELECT * FROM order_items WHERE order_id=?", (order_id,))
    order["shipping_address"] = row("SELECT * FROM shipping_addresses WHERE order_id=?", (order_id,))
    return order


@app.get("/api/orders")
@auth()
def my_orders():
    items = rows("SELECT id FROM orders WHERE user_id=? ORDER BY created_at DESC", (g.user["id"],))
    return jsonify(orders=[order_detail(item["id"]) for item in items])


@app.get("/api/orders/<int:order_id>")
@auth()
def order_by_id(order_id):
    order = row("SELECT * FROM orders WHERE id=?", (order_id,))
    if not order or (g.user["role"] != "admin" and order["user_id"] != g.user["id"]):
        return jsonify(error="Order not found"), 404
    return jsonify(order=order_detail(order_id))


@app.post("/api/products/<int:product_id>/reviews")
@auth()
def create_review(product_id):
    data = request.get_json(silent=True) or {}
    if not isinstance(data.get("rating"), int) or not 1 <= data["rating"] <= 5:
        return jsonify(error="Rating must be between 1 and 5"), 400
    delivered = row("""SELECT o.id FROM orders o JOIN order_items oi ON oi.order_id=o.id
                       WHERE o.user_id=? AND oi.product_id=? AND o.status='Delivered' LIMIT 1""",
                    (g.user["id"], product_id))
    if not delivered:
        return jsonify(error="Only customers who received this product can review it"), 403
    existing = row("SELECT id FROM reviews WHERE user_id=? AND product_id=? AND order_id=?",
                   (g.user["id"], product_id, delivered["id"]))
    if existing:
        return jsonify(error="You already reviewed this product for this order"), 409
    cursor = get_db().execute("""INSERT INTO reviews(user_id,product_id,order_id,rating,title,comment,status)
                                 VALUES(?,?,?,?,?,?,'Pending')""",
                              (g.user["id"], product_id, delivered["id"], data["rating"],
                               data.get("title"), data.get("comment")))
    get_db().commit()
    return jsonify(review=row("SELECT * FROM reviews WHERE id=?", (cursor.lastrowid,))), 201


@app.post("/api/contact")
def contact():
    data = request.get_json(silent=True) or {}
    error = validate(data, {"name": str, "email": str, "message": str})
    if error:
        return jsonify(error=error), 400
    cursor = get_db().execute("INSERT INTO contact_messages(name,email,message) VALUES(?,?,?)",
                              (data["name"].strip(), data["email"].strip(), data["message"].strip()))
    get_db().commit()
    return jsonify(message="Thank you. Your message has been sent.", id=cursor.lastrowid), 201


@app.get("/api/admin/dashboard")
@auth("admin")
def admin_dashboard():
    return jsonify(
        products=row("SELECT COUNT(*) value FROM products")["value"],
        customers=row("SELECT COUNT(*) value FROM users WHERE role_id=1")["value"],
        orders=row("SELECT COUNT(*) value FROM orders")["value"],
        pending=row("SELECT COUNT(*) value FROM orders WHERE status='Pending'")["value"],
        completed=row("SELECT COUNT(*) value FROM orders WHERE status='Delivered'")["value"],
        revenue=row("SELECT COALESCE(SUM(total),0) value FROM orders WHERE status!='Cancelled'")["value"],
        low_stock=rows("SELECT id,name,stock FROM products WHERE active=1 AND stock<=5 ORDER BY stock,name LIMIT 10"),
        recent_orders=rows("""SELECT o.id,o.order_number,o.status,o.total,o.created_at,u.name customer
                              FROM orders o LEFT JOIN users u ON u.id=o.user_id
                              ORDER BY o.created_at DESC LIMIT 8"""),
        sales=rows("""SELECT substr(created_at,1,10) date, SUM(total) total FROM orders
                      WHERE status!='Cancelled' AND created_at>=date('now','-30 days')
                      GROUP BY substr(created_at,1,10) ORDER BY date"""),
    )


@app.get("/api/admin/orders")
@auth("admin")
def admin_orders():
    search = request.args.get("search", "").strip()
    query = """SELECT o.*,u.name customer_name,u.email customer_email FROM orders o
               LEFT JOIN users u ON u.id=o.user_id"""
    params = []
    if search:
        query += " WHERE o.order_number LIKE ? OR u.name LIKE ? OR u.email LIKE ?"
        params = [f"%{search}%"] * 3
    query += " ORDER BY o.created_at DESC"
    return jsonify(orders=[order_detail(r["id"]) for r in rows(query, params)])


@app.put("/api/admin/orders/<int:order_id>/status")
@auth("admin")
def admin_order_status(order_id):
    data = request.get_json(silent=True) or {}
    status = data.get("status")
    allowed = {"Pending", "Confirmed", "Processing", "Shipped", "Delivered", "Cancelled"}
    if status not in allowed:
        return jsonify(error="Invalid status"), 400
    if not row("SELECT id FROM orders WHERE id=?", (order_id,)):
        return jsonify(error="Order not found"), 404
    old = row("SELECT status FROM orders WHERE id=?", (order_id,))["status"]
    db = get_db()
    db.execute("UPDATE orders SET status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (status, order_id))
    if old != "Cancelled" and status == "Cancelled":
        for item in rows("SELECT product_id,quantity FROM order_items WHERE order_id=? AND product_id IS NOT NULL", (order_id,)):
            db.execute("UPDATE products SET stock=stock+? WHERE id=?", (item["quantity"], item["product_id"]))
    elif old == "Cancelled" and status != "Cancelled":
        for item in rows("SELECT product_id,quantity FROM order_items WHERE order_id=? AND product_id IS NOT NULL", (order_id,)):
            db.execute("UPDATE products SET stock=stock-? WHERE id=? AND stock>=?", (item["quantity"], item["product_id"], item["quantity"]))
    db.commit()
    return jsonify(order=order_detail(order_id))


@app.get("/api/admin/customers")
@auth("admin")
def admin_customers():
    return jsonify(customers=rows("""SELECT u.id,u.name,u.email,u.phone,u.active,u.created_at,
                                     COUNT(o.id) order_count FROM users u LEFT JOIN orders o ON o.user_id=u.id
                                     WHERE u.role_id=1 GROUP BY u.id ORDER BY u.created_at DESC"""))


@app.put("/api/admin/customers/<int:user_id>")
@auth("admin")
def admin_customer_update(user_id):
    data = request.get_json(silent=True) or {}
    if not row("SELECT id FROM users WHERE id=? AND role_id=1", (user_id,)):
        return jsonify(error="Customer not found"), 404
    if "active" in data:
        get_db().execute("UPDATE users SET active=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (int(bool(data["active"])), user_id))
        get_db().commit()
    return jsonify(customer=row("SELECT id,name,email,phone,active FROM users WHERE id=?", (user_id,)))


@app.get("/api/admin/reviews")
@auth("admin")
def admin_reviews():
    return jsonify(reviews=rows("""SELECT r.*,u.name customer_name,p.name product_name
                                   FROM reviews r JOIN users u ON u.id=r.user_id JOIN products p ON p.id=r.product_id
                                   ORDER BY r.created_at DESC"""))


@app.put("/api/admin/reviews/<int:review_id>")
@auth("admin")
def admin_review(review_id):
    status = (request.get_json(silent=True) or {}).get("status")
    if status not in {"Pending", "Approved", "Rejected"}:
        return jsonify(error="Invalid review status"), 400
    get_db().execute("UPDATE reviews SET status=? WHERE id=?", (status, review_id))
    get_db().commit()
    return jsonify(review=row("SELECT * FROM reviews WHERE id=?", (review_id,)))


@app.delete("/api/admin/reviews/<int:review_id>")
@auth("admin")
def delete_review(review_id):
    get_db().execute("DELETE FROM reviews WHERE id=?", (review_id,))
    get_db().commit()
    return "", 204


@app.get("/api/admin/contact")
@auth("admin")
def admin_contact():
    return jsonify(messages=rows("SELECT * FROM contact_messages ORDER BY created_at DESC"))


@app.route("/api/admin/settings", methods=["GET", "PUT"])
@auth("admin")
def admin_settings():
    db = get_db()
    if request.method == "PUT":
        data = request.get_json(silent=True) or {}
        for key, value in data.items():
            if key.isidentifier():
                db.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                           (key, str(value)))
        db.commit()
    return jsonify(settings={r["key"]: r["value"] for r in db.execute("SELECT * FROM settings")})


@app.route("/api/admin/coupons", methods=["GET", "POST"])
@auth("admin")
def coupons():
    if request.method == "GET":
        return jsonify(coupons=rows("SELECT * FROM coupons ORDER BY id DESC"))
    data = request.get_json(silent=True) or {}
    if not data.get("code") or data.get("value") is None:
        return jsonify(error="Code and value are required"), 400
    cursor = get_db().execute("""INSERT INTO coupons(code,type,value,minimum_amount,active,expires_at)
                                 VALUES(?,?,?,?,?,?)""",
                              (data["code"].strip().upper(), data.get("type", "percentage"),
                               data["value"], data.get("minimum_amount", 0), int(bool(data.get("active", True))),
                               data.get("expires_at")))
    get_db().commit()
    return jsonify(coupon=row("SELECT * FROM coupons WHERE id=?", (cursor.lastrowid,))), 201


@app.route("/api/admin/coupons/<int:coupon_id>", methods=["PUT", "DELETE"])
@auth("admin")
def coupon_detail(coupon_id):
    db = get_db()
    if request.method == "DELETE":
        db.execute("DELETE FROM coupons WHERE id=?", (coupon_id,))
        db.commit()
        return "", 204
    data = request.get_json(silent=True) or {}
    if not row("SELECT id FROM coupons WHERE id=?", (coupon_id,)):
        return jsonify(error="Coupon not found"), 404
    db.execute("""UPDATE coupons SET code=COALESCE(?,code),type=COALESCE(?,type),value=COALESCE(?,value),
                  minimum_amount=COALESCE(?,minimum_amount),active=COALESCE(?,active),expires_at=COALESCE(?,expires_at)
                  WHERE id=?""", (data.get("code"), data.get("type"), data.get("value"), data.get("minimum_amount"),
                                  data.get("active"), data.get("expires_at"), coupon_id))
    db.commit()
    return jsonify(coupon=row("SELECT * FROM coupons WHERE id=?", (coupon_id,)))


@app.route("/api/admin/banners", methods=["GET", "POST"])
@auth("admin")
def banners():
    db = get_db()
    if request.method == "GET":
        return jsonify(banners=rows("SELECT * FROM banners ORDER BY sort_order,id"))
    data = request.get_json(silent=True) or {}
    if not data.get("title"):
        return jsonify(error="Title is required"), 400
    cursor = db.execute("""INSERT INTO banners(title,subtitle,image,link,active,sort_order)
                           VALUES(?,?,?,?,?,?)""", (data["title"], data.get("subtitle"), data.get("image"),
                                                    data.get("link"), int(data.get("active", True)),
                                                    data.get("sort_order", 0)))
    db.commit()
    return jsonify(banner=row("SELECT * FROM banners WHERE id=?", (cursor.lastrowid,))), 201


@app.route("/api/admin/banners/<int:banner_id>", methods=["PUT", "DELETE"])
@auth("admin")
def banner_detail(banner_id):
    db = get_db()
    if request.method == "DELETE":
        db.execute("DELETE FROM banners WHERE id=?", (banner_id,))
        db.commit()
        return "", 204
    data = request.get_json(silent=True) or {}
    db.execute("""UPDATE banners SET title=COALESCE(?,title),subtitle=COALESCE(?,subtitle),
                  image=COALESCE(?,image),link=COALESCE(?,link),active=COALESCE(?,active),
                  sort_order=COALESCE(?,sort_order) WHERE id=?""",
               (data.get("title"), data.get("subtitle"), data.get("image"), data.get("link"),
                data.get("active"), data.get("sort_order"), banner_id))
    db.commit()
    return jsonify(banner=row("SELECT * FROM banners WHERE id=?", (banner_id,)))


@app.route("/api/admin/contact/<int:message_id>", methods=["PUT", "DELETE"])
@auth("admin")
def contact_detail(message_id):
    db = get_db()
    if request.method == "DELETE":
        db.execute("DELETE FROM contact_messages WHERE id=?", (message_id,))
        db.commit()
        return "", 204
    status = (request.get_json(silent=True) or {}).get("status")
    if status not in {"Unread", "Read", "Replied"}:
        return jsonify(error="Invalid message status"), 400
    db.execute("UPDATE contact_messages SET status=? WHERE id=?", (status, message_id))
    db.commit()
    return jsonify(message=row("SELECT * FROM contact_messages WHERE id=?", (message_id,)))


@app.get("/api/payment-methods")
def payment_methods():
    enabled = row("SELECT value FROM settings WHERE key='payment_methods'")
    methods = enabled["value"].split(",") if enabled else "Cash on Delivery, Bank Transfer"
    if isinstance(methods, str):
        methods = [item.strip() for item in methods.split(",")]
    return jsonify(methods=methods)


@app.get("/api/admin/report")
@auth("admin")
def sales_report():
    return jsonify(report=rows("""SELECT o.order_number,o.created_at,u.name customer,o.status,o.total,o.payment_status
                                  FROM orders o LEFT JOIN users u ON u.id=o.user_id ORDER BY o.created_at DESC"""))


@app.get("/api/profile")
@auth()
def get_profile():
    return jsonify(user=user_public(g.user))


@app.put("/api/profile")
@auth()
def update_profile():
    data = request.get_json(silent=True) or {}
    if "email" in data and data["email"].strip().lower() != g.user["email"]:
        return jsonify(error="Email changes are not available here"), 400
    db = get_db()
    db.execute("UPDATE users SET name=COALESCE(?,name),phone=COALESCE(?,phone),updated_at=CURRENT_TIMESTAMP WHERE id=?",
               (data.get("name"), data.get("phone"), g.user["id"]))
    if data.get("password"):
        if len(data["password"]) < 8:
            return jsonify(error="Password must be at least 8 characters"), 400
        db.execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(data["password"]), g.user["id"]))
    db.commit()
    return jsonify(user=user_public(row("SELECT users.*,roles.name role FROM users JOIN roles ON roles.id=users.role_id WHERE users.id=?",
                                        (g.user["id"],))))


@app.errorhandler(400)
@app.errorhandler(404)
@app.errorhandler(500)
def error_response(error):
    return jsonify(error=getattr(error, "description", str(error))), error.code


init_db()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=os.environ.get("FLASK_DEBUG") == "1")
