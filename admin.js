async function adminLoad(view="dashboard") {
  if (!API.token || API.user()?.role !== "admin") return location.href = "login.html";
  document.getElementById("admin-title").textContent = view[0].toUpperCase()+view.slice(1);
  const content=document.getElementById("admin-content");
  try {
    if(view==="dashboard"){
      const d=await API.request("/api/admin/dashboard"); content.innerHTML=`<div class="stat-grid"><article><span>Revenue</span><strong>PKR ${Number(d.revenue).toLocaleString()}</strong></article><article><span>Orders</span><strong>${d.orders}</strong></article><article><span>Products</span><strong>${d.products}</strong></article><article><span>Customers</span><strong>${d.customers}</strong></article><article><span>Pending</span><strong>${d.pending}</strong></article><article><span>Delivered</span><strong>${d.completed}</strong></article></div><h2>Recent orders</h2>${d.recent_orders.map(o=>`<div class="table-row"><strong>${o.order_number}</strong><span>${o.customer||"Guest"}</span><span>${o.status}</span><strong>PKR ${Number(o.total).toLocaleString()}</strong></div>`).join("")}`;
    } else if(view==="products"){
      const {products}=await API.request("/api/products"); content.innerHTML=`<div class="admin-actions"><button class="primary-button" id="new-category">Add category</button><button class="primary-button" id="new-product">Add product</button></div><div class="admin-table">${products.map(p=>`<div class="table-row"><strong>${escapeHtml(p.name)}</strong><span>PKR ${p.price}</span><span>Stock ${p.stock}</span><button data-delete-product="${p.id}" class="danger">Delete</button></div>`).join("")}</div>`;
      document.getElementById("new-product").onclick=addProduct;
      document.getElementById("new-category").onclick=addCategory;
    } else if(view==="orders"){
      const {orders}=await API.request("/api/admin/orders");content.innerHTML=orders.map(o=>`<div class="order-admin"><strong>${o.order_number}</strong><span>${o.customer_name||"Guest"}</span><select data-order-status="${o.id}">${["Pending","Confirmed","Processing","Shipped","Delivered","Cancelled"].map(s=>`<option ${s===o.status?"selected":""}>${s}</option>`).join("")}</select></div>`).join("");
    } else if(view==="customers"){
      const {customers}=await API.request("/api/admin/customers");content.innerHTML=customers.map(c=>`<div class="table-row"><strong>${escapeHtml(c.name)}</strong><span>${escapeHtml(c.email)}</span><button data-customer="${c.id}" data-active="${!c.active}">${c.active?"Deactivate":"Activate"}</button></div>`).join("");
    } else { const {reviews}=await API.request("/api/admin/reviews");content.innerHTML=reviews.map(r=>`<div class="review-admin"><strong>${escapeHtml(r.product_name)}</strong><span>${escapeHtml(r.status)}</span><button data-review="${r.id}" data-status="Approved">Approve</button><button data-review="${r.id}" data-status="Rejected">Reject</button></div>`).join(""); }
  } catch(error){content.innerHTML=`<p class="empty-state">${error.message}</p>`}
}
async function addProduct(){
  const name=prompt("Product name"); if(!name)return; const price=prompt("Price","0"); const stock=prompt("Stock","0"); const categories=await API.request("/api/categories");
  if(!categories.categories.length)return alert("Create a category before adding a product.");
  const category=prompt("Category ID\nAvailable: "+categories.categories.map(c=>`${c.id}: ${c.name}`).join(", "));
  try{await API.request("/api/products",{method:"POST",body:JSON.stringify({name,price:Number(price),stock:Number(stock),category_id:Number(category),description:""})});adminLoad("products")}catch(error){alert(error.message)}
}
async function addCategory() {
  const name = prompt("Category name");
  if (!name) return;
  try {
    await API.request("/api/categories", { method:"POST", body:JSON.stringify({name, active:true}) });
    alert("Category created.");
  } catch(error) { alert(error.message); }
}
document.querySelectorAll("[data-admin-view]").forEach(b=>b.onclick=()=>adminLoad(b.dataset.adminView));
document.getElementById("admin-logout").onclick=()=>API.logout();
document.addEventListener("change",async e=>{if(e.target.dataset.orderStatus){try{await API.request("/api/admin/orders/"+e.target.dataset.orderStatus+"/status",{method:"PUT",body:JSON.stringify({status:e.target.value})})}catch(error){alert(error.message)}}});
document.addEventListener("click",async e=>{try{if(e.target.dataset.deleteProduct){if(confirm("Delete this product?")){await API.request("/api/products/"+e.target.dataset.deleteProduct,{method:"DELETE"});adminLoad("products")}}if(e.target.dataset.customer){await API.request("/api/admin/customers/"+e.target.dataset.customer,{method:"PUT",body:JSON.stringify({active:e.target.dataset.active==="true"})});adminLoad("customers")}if(e.target.dataset.review){await API.request("/api/admin/reviews/"+e.target.dataset.review,{method:"PUT",body:JSON.stringify({status:e.target.dataset.status})});adminLoad("reviews")}}catch(error){alert(error.message)}});
adminLoad();
