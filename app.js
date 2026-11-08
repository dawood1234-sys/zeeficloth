const API = {
  token: localStorage.getItem("zeefi_token"),
  async request(path, options = {}) {
    const headers = { ...(options.headers || {}) };
    if (!(options.body instanceof FormData)) headers["Content-Type"] = "application/json";
    if (this.token) headers.Authorization = `Bearer ${this.token}`;
    const response = await fetch(path, { ...options, headers });
    const body = response.status === 204 ? {} : await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.error || "Request failed");
    return body;
  },
  setSession(token, user) {
    this.token = token;
    localStorage.setItem("zeefi_token", token);
    localStorage.setItem("zeefi_user", JSON.stringify(user));
  },
  logout() {
    this.token = null;
    localStorage.removeItem("zeefi_token");
    localStorage.removeItem("zeefi_user");
    location.href = "index.html";
  },
  user() {
    try { return JSON.parse(localStorage.getItem("zeefi_user")); } catch { return null; }
  }
};

async function loadCatalog(containerId = "product-grid") {
  const container = document.getElementById(containerId);
  if (!container) return;
  container.innerHTML = '<div class="loading">Loading collection...</div>';
  try {
    const params = new URLSearchParams(location.search);
    params.delete("p");
    const { products } = await API.request(`/api/products${params.toString() ? "?" + params : ""}`);
    container.innerHTML = products.length ? products.map(productCard).join("") : '<p class="empty-state">No products found.</p>';
  } catch (error) {
    container.innerHTML = `<p class="empty-state">${error.message}</p>`;
  }
}

function productCard(product) {
  const image = product.images[0]?.url || "https://images.unsplash.com/photo-1483985988355-763728e1935b";
  const price = product.discount_price ?? product.price;
  return `<article class="product-card">
    <a href="product.html?id=${product.id}" class="product-image">
      <img src="${image}" alt="${escapeHtml(product.name)}">
    </a>
    <div class="product-info">
      <p class="eyebrow">${escapeHtml(product.category_name || "Collection")}</p>
      <h3><a href="product.html?id=${product.id}">${escapeHtml(product.name)}</a></h3>
      <div class="price-row">
        <strong>PKR ${Number(price).toLocaleString()}</strong>
        ${product.discount_price ? `<del>PKR ${Number(product.price).toLocaleString()}</del>` : ""}
      </div>
      <button class="icon-button wishlist-toggle" data-product="${product.id}" aria-label="Add to wishlist">♡</button>
    </div>
  </article>`;
}

function escapeHtml(value = "") {
  const element = document.createElement("div");
  element.textContent = value;
  return element.innerHTML;
}

document.addEventListener("click", async event => {
  const wishlist = event.target.closest(".wishlist-toggle");
  if (wishlist) {
    if (!API.token) return location.href = "login.html";
    try {
      await API.request(`/api/wishlist/${wishlist.dataset.product}`, { method: "POST" });
      wishlist.textContent = "♥";
      wishlist.classList.add("active");
    } catch (error) { alert(error.message); }
  }
});

function renderNav() {
  const user = API.user();
  document.querySelectorAll("[data-auth-link]").forEach(link => {
    if (user) {
      link.textContent = user.role === "admin" ? "Admin" : "Account";
      link.href = user.role === "admin" ? "admin.html" : "account.html";
    }
  });
}

async function loadProduct() {
  const container = document.getElementById("product-detail");
  const id = new URLSearchParams(location.search).get("id");
  if (!container || !id) return;
  try {
    const { product, reviews } = await API.request(`/api/products/${id}`);
    container.innerHTML = `<div class="product-gallery"><img src="${product.images[0]?.url || ""}" alt="${escapeHtml(product.name)}"></div>
      <div class="product-copy">
        <p class="eyebrow">${escapeHtml(product.category_name || "")}</p><h1>${escapeHtml(product.name)}</h1>
        <div class="detail-price">PKR ${Number(product.discount_price ?? product.price).toLocaleString()}</div>
        <p>${escapeHtml(product.description)}</p>
        <label>Size<select id="size"><option value="">Select size</option>${product.sizes.map(s => `<option>${escapeHtml(s.name)}</option>`).join("")}</select></label>
        <label>Color<select id="color"><option value="">Select color</option>${product.colors.map(c => `<option>${escapeHtml(c.name)}</option>`).join("")}</select></label>
        <label>Quantity<input id="quantity" type="number" value="1" min="1" max="${product.stock}"></label>
        <button class="primary-button" id="add-cart">Add to bag</button>
        <p class="stock">${product.stock > 0 ? `${product.stock} in stock` : "Out of stock"}</p>
      </div>
      <section class="reviews"><h2>Customer reviews</h2>${reviews.length ? reviews.map(r => `<article><strong>${escapeHtml(r.name)}</strong> — ${"★".repeat(r.rating)}<p>${escapeHtml(r.comment || "")}</p></article>`).join("") : "<p>Be the first to review this product after delivery.</p>"}</section>`;
    document.getElementById("add-cart").onclick = async () => {
      if (!API.token) return location.href = "login.html";
      try {
        await API.request("/api/cart", { method: "POST", body: JSON.stringify({ product_id: product.id, quantity: Number(document.getElementById("quantity").value), size: document.getElementById("size").value || null, color: document.getElementById("color").value || null }) });
        location.href = "cart.html";
      } catch (error) { alert(error.message); }
    };
  } catch (error) { container.innerHTML = `<p class="empty-state">${error.message}</p>`; }
}

renderNav();
loadCatalog();
loadCatalog("featured-grid");
loadProduct();
