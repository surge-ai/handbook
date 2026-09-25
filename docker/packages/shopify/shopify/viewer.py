"""Shopify viewer — read-only product catalog UI and API endpoints.

Serves:
  GET /api/products        — product list with summary info (supports ?search=X&type=X&vendor=X)
  GET /api/products/:id    — product detail with all variants
  GET /api/carts           — list all carts
  GET /api/carts/:id       — cart detail with line items
  GET /api/policies        — list policies
  GET /                    — viewer HTML (single-page app)

All non-MCP routes require the X-Proxy-Token header.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route

# ---------------------------------------------------------------------------
# Auth middleware
# ---------------------------------------------------------------------------


class ProxyTokenMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith("/mcp"):
            return await call_next(request)
        token = os.environ.get("MCP_PROXY_TOKEN", "")
        if token and request.headers.get("x-proxy-token") != token:
            return Response("Forbidden: invalid proxy token", status_code=403)
        return await call_next(request)


# ---------------------------------------------------------------------------
# API handlers
# ---------------------------------------------------------------------------


async def api_products(request: Request) -> JSONResponse:
    from shopify.models import SearchFilter
    from shopify.state import get_state, search_products

    search = request.query_params.get("search", "").strip()
    type_filter = request.query_params.get("type", "").strip()
    vendor_filter = request.query_params.get("vendor", "").strip()

    state = get_state()

    if search:
        filters = []
        if type_filter:
            filters.append(SearchFilter(productType=type_filter))
        if vendor_filter:
            filters.append(SearchFilter(productVendor=vendor_filter))
        products, _, _, _ = search_products(search, filters=filters or None, limit=250)
    else:
        products = list(state.products.values())
        if type_filter:
            products = [p for p in products if p.productType.lower() == type_filter.lower()]
        if vendor_filter:
            products = [p for p in products if p.vendor.lower() == vendor_filter.lower()]

    return JSONResponse(
        {
            "products": [_format_product_summary(p) for p in products],
            "total": len(products),
        }
    )


async def api_product_detail(request: Request) -> JSONResponse:
    from shopify.state import get_product_by_id

    product_id = request.path_params["product_id"]
    product = get_product_by_id(product_id)
    if product is None:
        return JSONResponse({"error": "Product not found"}, status_code=404)
    return JSONResponse({"product": _format_product_full(product)})


async def api_carts(request: Request) -> JSONResponse:
    from shopify.state import get_all_carts

    carts = get_all_carts()
    return JSONResponse(
        {
            "carts": [_format_cart_summary(c) for c in carts],
            "total": len(carts),
        }
    )


async def api_cart_detail(request: Request) -> JSONResponse:
    from shopify.state import get_cart_by_id

    cart_id = request.path_params["cart_id"]
    cart = get_cart_by_id(cart_id)
    if cart is None:
        return JSONResponse({"error": "Cart not found"}, status_code=404)
    return JSONResponse({"cart": _as_json_value(cart)})


async def api_policies(request: Request) -> JSONResponse:
    from shopify.state import get_state

    state = get_state()
    return JSONResponse({"policies": _as_json_value(state.policies)})


async def viewer_html(request: Request) -> HTMLResponse:
    return HTMLResponse(VIEWER_HTML)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_product_summary(product: Any) -> dict[str, Any]:
    p: dict[str, Any] = _as_json_dict(product)
    price_range = p.get("priceRange") or {}
    min_price = price_range.get("minVariantPrice", {})
    images = p.get("images", [])
    image_url = images[0].get("url") if images else None
    return {
        "id": p.get("id"),
        "title": p.get("title"),
        "vendor": p.get("vendor"),
        "productType": p.get("productType"),
        "tags": p.get("tags", []),
        "availableForSale": p.get("availableForSale", True),
        "price": min_price.get("amount"),
        "currencyCode": min_price.get("currencyCode", "USD"),
        "image": image_url,
        "handle": p.get("handle"),
    }


def _format_product_full(product: Any) -> dict[str, Any]:
    p: dict[str, Any] = _as_json_dict(product)
    d = _format_product_summary(p)
    d["description"] = p.get("description", "")
    d["variants"] = p.get("variants", [])
    d["images"] = p.get("images", [])
    d["options"] = p.get("options", [])
    d["priceRange"] = p.get("priceRange", {})
    return d


def _format_cart_summary(cart: Any) -> dict[str, Any]:
    c: dict[str, Any] = _as_json_dict(cart)
    return {
        "id": c.get("id"),
        "totalQuantity": c.get("totalQuantity", 0),
        "itemCount": len(c.get("lines", [])),
        "totalAmount": c.get("cost", {}).get("totalAmount", {}),
        "createdAt": c.get("createdAt"),
        "updatedAt": c.get("updatedAt"),
        "note": c.get("note"),
        "checkoutUrl": c.get("checkoutUrl"),
    }


def _as_json_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True, exclude_none=True)
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _as_json_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True, exclude_none=True)
    if isinstance(value, Mapping):
        return {key: _as_json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_as_json_value(item) for item in value]
    return value


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_shopify_viewer_app():
    routes = [
        Route("/", viewer_html),
        Route("/api/products", api_products),
        Route("/api/products/{product_id:path}", api_product_detail),
        Route("/api/carts", api_carts),
        Route("/api/carts/{cart_id:path}", api_cart_detail),
        Route("/api/policies", api_policies),
    ]
    return Starlette(
        routes=routes,
        middleware=[Middleware(ProxyTokenMiddleware)],
    )


def run_http_server(mcp_app, port: int) -> None:
    """Run combined MCP + viewer HTTP server."""
    fastmcp_asgi = mcp_app.http_app(
        transport="streamable-http",
        path="/mcp",
    )

    viewer = create_shopify_viewer_app()

    async def combined_app(scope, receive, send):
        if scope["type"] == "lifespan":
            await fastmcp_asgi(scope, receive, send)
            return
        path = scope.get("path", "")
        if path.startswith("/mcp"):
            await fastmcp_asgi(scope, receive, send)
        else:
            await viewer(scope, receive, send)

    uvicorn.run(
        combined_app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
    )


# ---------------------------------------------------------------------------
# Viewer HTML
# ---------------------------------------------------------------------------

VIEWER_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Shopify Catalog</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f6f6f7; color: #202223; display: flex; flex-direction: column; height: 100vh; }

  /* Top nav */
  .topnav { background: #1a1a2e; color: #fff; display: flex; align-items: center; padding: 0 20px; height: 52px; gap: 24px; flex-shrink: 0; }
  .topnav .brand { font-size: 15px; font-weight: 700; color: #fff; letter-spacing: -0.3px; }
  .topnav .brand span { color: #95bf47; }
  .nav-links { display: flex; gap: 4px; }
  .nav-link { padding: 6px 14px; border-radius: 6px; cursor: pointer; font-size: 13px; color: #c9ccd1; border: none; background: none; }
  .nav-link:hover { background: rgba(255,255,255,0.1); color: #fff; }
  .nav-link.active { background: rgba(255,255,255,0.15); color: #fff; font-weight: 600; }
  .nav-spacer { flex: 1; }
  .cart-badge { background: #95bf47; color: #fff; font-size: 11px; font-weight: 700; border-radius: 10px; padding: 2px 7px; }

  /* Main layout */
  .main { display: flex; flex: 1; overflow: hidden; }

  /* Sidebar */
  .sidebar { width: 220px; min-width: 220px; background: #fff; border-right: 1px solid #e1e3e5; display: flex; flex-direction: column; overflow-y: auto; }
  .sidebar-section { padding: 16px; border-bottom: 1px solid #e1e3e5; }
  .sidebar-section h3 { font-size: 11px; font-weight: 600; color: #6d7175; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 10px; }
  .filter-item { display: flex; align-items: center; gap: 8px; padding: 5px 0; cursor: pointer; font-size: 13px; color: #202223; }
  .filter-item input[type=radio], .filter-item input[type=checkbox] { accent-color: #008060; }
  .filter-item.active { color: #008060; font-weight: 600; }
  .filter-count { margin-left: auto; font-size: 11px; color: #8c9196; }

  /* Content area */
  .content { flex: 1; display: flex; overflow: hidden; }

  /* Product list */
  .product-list-pane { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
  .list-toolbar { padding: 12px 20px; background: #fff; border-bottom: 1px solid #e1e3e5; display: flex; align-items: center; gap: 12px; flex-shrink: 0; }
  .list-toolbar input { flex: 1; padding: 8px 12px; border: 1px solid #8c9196; border-radius: 6px; font-size: 13px; }
  .list-toolbar input:focus { outline: none; border-color: #008060; box-shadow: 0 0 0 2px rgba(0,128,96,0.15); }
  .results-count { font-size: 13px; color: #6d7175; white-space: nowrap; }
  .view-toggle { display: flex; gap: 2px; }
  .view-btn { padding: 6px 8px; border: 1px solid #d1d5db; background: #fff; cursor: pointer; font-size: 14px; color: #6d7175; }
  .view-btn:first-child { border-radius: 6px 0 0 6px; }
  .view-btn:last-child { border-radius: 0 6px 6px 0; }
  .view-btn.active { background: #f1f2f3; color: #202223; }

  .products-scroll { flex: 1; overflow-y: auto; padding: 20px; }

  /* Product grid */
  .products-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 16px; }
  .product-card { background: #fff; border: 1px solid #e1e3e5; border-radius: 8px; cursor: pointer; overflow: hidden; transition: box-shadow 0.15s; }
  .product-card:hover { box-shadow: 0 4px 12px rgba(0,0,0,0.1); }
  .product-card.active { border-color: #008060; box-shadow: 0 0 0 2px rgba(0,128,96,0.2); }
  .product-thumb { width: 100%; aspect-ratio: 1; background: #f1f2f3; display: flex; align-items: center; justify-content: center; overflow: hidden; }
  .product-thumb img { width: 100%; height: 100%; object-fit: cover; }
  .product-thumb-placeholder { font-size: 36px; color: #8c9196; }
  .product-info { padding: 12px; }
  .product-title { font-size: 13px; font-weight: 600; color: #202223; margin-bottom: 4px; overflow: hidden; text-overflow: ellipsis; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
  .product-vendor { font-size: 11px; color: #6d7175; margin-bottom: 6px; }
  .product-price { font-size: 14px; font-weight: 700; color: #202223; }
  .product-type-badge { display: inline-block; font-size: 10px; padding: 2px 7px; background: #f1f2f3; border-radius: 10px; color: #6d7175; margin-top: 6px; }
  .avail-dot { display: inline-block; width: 7px; height: 7px; border-radius: 50%; margin-right: 4px; }
  .avail-dot.yes { background: #95bf47; }
  .avail-dot.no { background: #d72c0d; }

  /* Product list (rows) */
  .products-list { display: flex; flex-direction: column; gap: 2px; }
  .product-row { background: #fff; border: 1px solid #e1e3e5; border-radius: 6px; display: flex; align-items: center; gap: 14px; padding: 10px 14px; cursor: pointer; }
  .product-row:hover { background: #f6f6f7; }
  .product-row.active { border-color: #008060; background: #f0faf7; }
  .product-row-thumb { width: 44px; height: 44px; border-radius: 4px; background: #f1f2f3; display: flex; align-items: center; justify-content: center; overflow: hidden; flex-shrink: 0; font-size: 20px; color: #8c9196; }
  .product-row-thumb img { width: 100%; height: 100%; object-fit: cover; border-radius: 4px; }
  .product-row-info { flex: 1; min-width: 0; }
  .product-row-title { font-size: 13px; font-weight: 600; color: #202223; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .product-row-meta { font-size: 12px; color: #6d7175; margin-top: 2px; }
  .product-row-price { font-size: 13px; font-weight: 700; color: #202223; flex-shrink: 0; }
  .product-row-type { font-size: 11px; color: #6d7175; flex-shrink: 0; min-width: 80px; text-align: right; }

  /* Detail panel */
  .detail-panel { width: 380px; min-width: 380px; background: #fff; border-left: 1px solid #e1e3e5; display: flex; flex-direction: column; overflow-y: auto; }
  .detail-panel.hidden { display: none; }
  .detail-close { position: sticky; top: 0; background: #fff; z-index: 1; padding: 12px 16px; border-bottom: 1px solid #e1e3e5; display: flex; justify-content: space-between; align-items: center; }
  .detail-close h3 { font-size: 14px; font-weight: 600; }
  .close-btn { background: none; border: none; font-size: 18px; cursor: pointer; color: #6d7175; padding: 2px 6px; border-radius: 4px; }
  .close-btn:hover { background: #f1f2f3; }
  .detail-image { width: 100%; aspect-ratio: 1; background: #f1f2f3; display: flex; align-items: center; justify-content: center; overflow: hidden; font-size: 64px; color: #8c9196; }
  .detail-image img { width: 100%; height: 100%; object-fit: cover; }
  .detail-body { padding: 16px; }
  .detail-body h2 { font-size: 16px; font-weight: 700; margin-bottom: 4px; line-height: 1.3; }
  .detail-vendor { font-size: 12px; color: #6d7175; margin-bottom: 12px; }
  .detail-price-row { display: flex; align-items: baseline; gap: 8px; margin-bottom: 12px; }
  .detail-price { font-size: 22px; font-weight: 700; color: #202223; }
  .detail-avail { font-size: 12px; padding: 3px 8px; border-radius: 10px; }
  .detail-avail.yes { background: #e3f1da; color: #1c6b2a; }
  .detail-avail.no { background: #fce8e6; color: #7a0000; }
  .detail-desc { font-size: 13px; color: #4a4a4a; line-height: 1.6; margin-bottom: 16px; white-space: pre-wrap; }
  .detail-section-title { font-size: 11px; font-weight: 600; color: #6d7175; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px; }
  .variants-table { width: 100%; border-collapse: collapse; margin-bottom: 16px; }
  .variants-table th { font-size: 11px; font-weight: 600; color: #6d7175; text-align: left; padding: 6px 8px; border-bottom: 1px solid #e1e3e5; }
  .variants-table td { font-size: 12px; padding: 7px 8px; border-bottom: 1px solid #f1f2f3; }
  .variants-table tr:last-child td { border-bottom: none; }
  .tag-list { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 16px; }
  .tag { display: inline-block; font-size: 11px; padding: 3px 9px; background: #f1f2f3; border-radius: 10px; color: #6d7175; }
  .img-gallery { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 16px; }
  .img-gallery img { width: 60px; height: 60px; object-fit: cover; border-radius: 4px; border: 1px solid #e1e3e5; cursor: pointer; }
  .img-gallery img:hover { border-color: #008060; }

  /* Carts view */
  .carts-view { flex: 1; overflow-y: auto; padding: 20px; }
  .carts-view.hidden { display: none; }
  .carts-grid { display: flex; flex-direction: column; gap: 12px; }
  .cart-card { background: #fff; border: 1px solid #e1e3e5; border-radius: 8px; padding: 16px; cursor: pointer; }
  .cart-card:hover { box-shadow: 0 2px 8px rgba(0,0,0,0.08); }
  .cart-card.active { border-color: #008060; }
  .cart-id { font-size: 11px; color: #6d7175; margin-bottom: 8px; font-family: monospace; word-break: break-all; }
  .cart-meta { display: flex; gap: 16px; }
  .cart-stat { text-align: center; }
  .cart-stat .val { font-size: 18px; font-weight: 700; color: #202223; }
  .cart-stat .lbl { font-size: 11px; color: #6d7175; }
  .cart-total { font-size: 14px; font-weight: 700; margin-top: 8px; }

  /* Cart detail panel */
  .cart-lines { margin-top: 12px; }
  .cart-line { display: flex; align-items: center; gap: 10px; padding: 10px 0; border-bottom: 1px solid #f1f2f3; }
  .cart-line:last-child { border-bottom: none; }
  .cart-line-thumb { width: 40px; height: 40px; background: #f1f2f3; border-radius: 4px; display: flex; align-items: center; justify-content: center; font-size: 18px; color: #8c9196; flex-shrink: 0; overflow: hidden; }
  .cart-line-thumb img { width: 100%; height: 100%; object-fit: cover; border-radius: 4px; }
  .cart-line-info { flex: 1; min-width: 0; }
  .cart-line-name { font-size: 13px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .cart-line-variant { font-size: 11px; color: #6d7175; }
  .cart-line-qty { font-size: 12px; color: #6d7175; }
  .cart-line-price { font-size: 13px; font-weight: 700; flex-shrink: 0; }

  /* Policies view */
  .policies-view { flex: 1; overflow-y: auto; padding: 20px; }
  .policies-view.hidden { display: none; }
  .policy-card { background: #fff; border: 1px solid #e1e3e5; border-radius: 8px; padding: 20px; margin-bottom: 16px; }
  .policy-title { font-size: 16px; font-weight: 700; margin-bottom: 12px; }
  .policy-body { font-size: 13px; color: #4a4a4a; line-height: 1.7; white-space: pre-wrap; }

  /* Empty states */
  .empty { text-align: center; padding: 60px 20px; color: #8c9196; font-size: 14px; }
  .empty-icon { font-size: 40px; margin-bottom: 12px; }
</style>
</head>
<body>
  <div class="topnav">
    <div class="brand"><span>●</span> Shopify Catalog</div>
    <div class="nav-links">
      <button class="nav-link active" id="nav-products" onclick="showView('products')">Products</button>
      <button class="nav-link" id="nav-carts" onclick="showView('carts')">Carts <span id="cart-badge" class="cart-badge" style="display:none"></span></button>
      <button class="nav-link" id="nav-policies" onclick="showView('policies')">Policies</button>
    </div>
    <div class="nav-spacer"></div>
    <div id="total-count" style="font-size:12px;color:#8c9196;"></div>
  </div>

  <div class="main">
    <!-- Sidebar filters -->
    <div class="sidebar" id="sidebar">
      <div class="sidebar-section">
        <h3>Availability</h3>
        <label class="filter-item"><input type="radio" name="avail" value="" onchange="applyFilters()" checked> All</label>
        <label class="filter-item"><input type="radio" name="avail" value="true" onchange="applyFilters()"> Available</label>
        <label class="filter-item"><input type="radio" name="avail" value="false" onchange="applyFilters()"> Unavailable</label>
      </div>
      <div class="sidebar-section" id="type-section">
        <h3>Product Type</h3>
        <div id="type-filters"></div>
      </div>
      <div class="sidebar-section" id="vendor-section">
        <h3>Vendor</h3>
        <div id="vendor-filters"></div>
      </div>
    </div>

    <!-- Content -->
    <div class="content">
      <!-- Products view -->
      <div class="product-list-pane" id="products-view">
        <div class="list-toolbar">
          <input type="text" id="search-input" placeholder="Search products..." oninput="onSearch()">
          <span class="results-count" id="results-count"></span>
          <div class="view-toggle">
            <button class="view-btn active" id="view-grid" onclick="setView('grid')" title="Grid view">⊞</button>
            <button class="view-btn" id="view-list" onclick="setView('list')" title="List view">≡</button>
          </div>
        </div>
        <div class="products-scroll">
          <div id="products-container"><div class="empty"><div class="empty-icon">⏳</div>Loading...</div></div>
        </div>
      </div>

      <!-- Carts view -->
      <div class="carts-view hidden" id="carts-view">
        <div id="carts-container"><div class="empty"><div class="empty-icon">⏳</div>Loading...</div></div>
      </div>

      <!-- Policies view -->
      <div class="policies-view hidden" id="policies-view">
        <div id="policies-container"><div class="empty"><div class="empty-icon">⏳</div>Loading...</div></div>
      </div>

      <!-- Detail panel -->
      <div class="detail-panel hidden" id="detail-panel">
        <div class="detail-close">
          <h3 id="detail-panel-title">Product Detail</h3>
          <button class="close-btn" onclick="closeDetail()">✕</button>
        </div>
        <div id="detail-content"></div>
      </div>
    </div>
  </div>

<script>
  let allProducts = [];
  let filteredProducts = [];
  let selectedTypes = new Set();
  let selectedVendors = new Set();
  let viewMode = 'grid';
  let searchTimeout = null;
  let currentView = 'products';
  let selectedProductId = null;
  let selectedCartId = null;

  const base = window.location.pathname.replace(/\\/$/, '');

  async function fetchJSON(path) {
    const r = await fetch(base + path);
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return r.json();
  }

  function esc(s) {
    if (!s && s !== 0) return '';
    const d = document.createElement('div');
    d.textContent = String(s);
    return d.innerHTML;
  }

  function formatPrice(amount, currency) {
    if (!amount) return '';
    try {
      return new Intl.NumberFormat('en-US', { style: 'currency', currency: currency || 'USD' }).format(parseFloat(amount));
    } catch { return amount + ' ' + (currency || ''); }
  }

  function productColor(id) {
    const colors = ['#e8d5f5','#d5e8f5','#d5f5e8','#f5e8d5','#f5d5d5','#d5f5f5'];
    let hash = 0;
    for (let i = 0; i < (id || '').length; i++) hash = (hash * 31 + id.charCodeAt(i)) & 0xffff;
    return colors[hash % colors.length];
  }

  // ---- Products ----

  async function loadProducts() {
    try {
      const data = await fetchJSON('/api/products?_limit=500');
      allProducts = data.products || [];
      buildFilters();
      applyFilters();
      document.getElementById('total-count').textContent = allProducts.length + ' products';
    } catch (e) {
      document.getElementById('products-container').innerHTML = '<div class="empty"><div class="empty-icon">⚠️</div>Failed to load products</div>';
    }
  }

  function buildFilters() {
    const types = {};
    const vendors = {};
    allProducts.forEach(p => {
      if (p.productType) types[p.productType] = (types[p.productType] || 0) + 1;
      if (p.vendor) vendors[p.vendor] = (vendors[p.vendor] || 0) + 1;
    });

    const typeEl = document.getElementById('type-filters');
    typeEl.innerHTML = '<label class="filter-item"><input type="radio" name="ptype" value="" onchange="onTypeChange(\\'\\')" checked> All</label>' +
      Object.entries(types).sort().map(([t, c]) =>
        '<label class="filter-item"><input type="radio" name="ptype" value="' + esc(t) + '" onchange="onTypeChange(\\'' + esc(t) + '\\')">' +
        '<span>' + esc(t) + '</span><span class="filter-count">' + c + '</span></label>'
      ).join('');

    const vendorEl = document.getElementById('vendor-filters');
    vendorEl.innerHTML = '<label class="filter-item"><input type="radio" name="pvendor" value="" onchange="onVendorChange(\\'\\')" checked> All</label>' +
      Object.entries(vendors).sort().map(([v, c]) =>
        '<label class="filter-item"><input type="radio" name="pvendor" value="' + esc(v) + '" onchange="onVendorChange(\\'' + esc(v) + '\\')">' +
        '<span>' + esc(v) + '</span><span class="filter-count">' + c + '</span></label>'
      ).join('');
  }

  let activeType = '';
  let activeVendor = '';

  function onTypeChange(t) { activeType = t; applyFilters(); }
  function onVendorChange(v) { activeVendor = v; applyFilters(); }

  function applyFilters() {
    const search = (document.getElementById('search-input').value || '').toLowerCase();
    const availVal = document.querySelector('input[name=avail]:checked')?.value || '';

    filteredProducts = allProducts.filter(p => {
      if (activeType && (p.productType || '') !== activeType) return false;
      if (activeVendor && (p.vendor || '') !== activeVendor) return false;
      if (availVal === 'true' && !p.availableForSale) return false;
      if (availVal === 'false' && p.availableForSale) return false;
      if (search) {
        const hay = ((p.title || '') + ' ' + (p.vendor || '') + ' ' + (p.productType || '') + ' ' + (p.tags || []).join(' ')).toLowerCase();
        if (!hay.includes(search)) return false;
      }
      return true;
    });

    document.getElementById('results-count').textContent = filteredProducts.length + ' of ' + allProducts.length;
    renderProducts();
  }

  function renderProducts() {
    const container = document.getElementById('products-container');
    if (!filteredProducts.length) {
      container.innerHTML = '<div class="empty"><div class="empty-icon">🔍</div>No products found</div>';
      return;
    }

    if (viewMode === 'grid') {
      container.innerHTML = '<div class="products-grid">' +
        filteredProducts.map(p => productCardHTML(p)).join('') + '</div>';
    } else {
      container.innerHTML = '<div class="products-list">' +
        filteredProducts.map(p => productRowHTML(p)).join('') + '</div>';
    }
  }

  function productCardHTML(p) {
    const price = formatPrice(p.price, p.currencyCode);
    const thumbHTML = p.image
      ? '<img src="' + esc(p.image) + '" alt="" onerror="this.parentNode.innerHTML=\\'<span class=product-thumb-placeholder>🛍️</span>\\'">'
      : '<span class="product-thumb-placeholder" style="background:' + productColor(p.id) + ';width:100%;height:100%;display:flex;align-items:center;justify-content:center;font-size:36px">🛍️</span>';
    const active = p.id === selectedProductId ? ' active' : '';
    return '<div class="product-card' + active + '" onclick="showProduct(\\'' + esc(p.id) + '\\')">' +
      '<div class="product-thumb">' + thumbHTML + '</div>' +
      '<div class="product-info">' +
        '<div class="product-title">' + esc(p.title) + '</div>' +
        '<div class="product-vendor">' + esc(p.vendor) + '</div>' +
        '<div class="product-price">' + price + '</div>' +
        (p.productType ? '<div class="product-type-badge">' + esc(p.productType) + '</div>' : '') +
      '</div>' +
    '</div>';
  }

  function productRowHTML(p) {
    const price = formatPrice(p.price, p.currencyCode);
    const thumbHTML = p.image
      ? '<img src="' + esc(p.image) + '" alt="" onerror="this.innerHTML=\\'🛍️\\'">'
      : '🛍️';
    const active = p.id === selectedProductId ? ' active' : '';
    const avail = p.availableForSale ? '<span class="avail-dot yes"></span>In stock' : '<span class="avail-dot no"></span>Out of stock';
    return '<div class="product-row' + active + '" onclick="showProduct(\\'' + esc(p.id) + '\\')">' +
      '<div class="product-row-thumb">' + thumbHTML + '</div>' +
      '<div class="product-row-info">' +
        '<div class="product-row-title">' + esc(p.title) + '</div>' +
        '<div class="product-row-meta">' + esc(p.vendor) + ' · ' + avail + '</div>' +
      '</div>' +
      '<div class="product-row-type">' + esc(p.productType) + '</div>' +
      '<div class="product-row-price">' + price + '</div>' +
    '</div>';
  }

  async function showProduct(id) {
    selectedProductId = id;
    renderProducts(); // re-render to highlight
    const panel = document.getElementById('detail-panel');
    const content = document.getElementById('detail-content');
    content.innerHTML = '<div class="empty">Loading...</div>';
    panel.classList.remove('hidden');

    try {
      const data = await fetchJSON('/api/products/' + encodeURIComponent(id));
      const p = data.product;
      renderProductDetail(p);
    } catch (e) {
      content.innerHTML = '<div class="empty">Failed to load product</div>';
    }
  }

  function renderProductDetail(p) {
    const content = document.getElementById('detail-content');
    document.getElementById('detail-panel-title').textContent = 'Product';

    const images = p.images || [];
    const mainImage = images[0]?.url;
    const price = formatPrice(p.price, p.currencyCode);
    const availClass = p.availableForSale ? 'yes' : 'no';
    const availText = p.availableForSale ? 'In stock' : 'Out of stock';

    let html = '';

    // Main image
    if (mainImage) {
      html += '<div class="detail-image"><img src="' + esc(mainImage) + '" alt="" onerror="this.parentNode.innerHTML=\\'<span>🛍️</span>\\'">' + '</div>';
    } else {
      const bg = productColor(p.id);
      html += '<div class="detail-image" style="background:' + bg + '">🛍️</div>';
    }

    html += '<div class="detail-body">';
    html += '<h2>' + esc(p.title) + '</h2>';
    html += '<div class="detail-vendor">' + esc(p.vendor) + (p.productType ? ' · ' + esc(p.productType) : '') + '</div>';
    html += '<div class="detail-price-row"><span class="detail-price">' + price + '</span><span class="detail-avail ' + availClass + '">' + availText + '</span></div>';

    if (p.description) {
      html += '<div class="detail-desc">' + esc(p.description) + '</div>';
    }

    // Variants
    const variants = p.variants || [];
    if (variants.length) {
      html += '<div class="detail-section-title">Variants (' + variants.length + ')</div>';
      html += '<table class="variants-table"><thead><tr><th>Title</th><th>SKU</th><th>Price</th><th>Qty</th></tr></thead><tbody>';
      variants.forEach(v => {
        const vp = v.price ? formatPrice(v.price.amount || v.price, v.price.currencyCode) : '';
        const qty = v.quantityAvailable !== undefined && v.quantityAvailable !== null ? v.quantityAvailable : '—';
        html += '<tr><td>' + esc(v.title) + '</td><td style="font-family:monospace;font-size:11px">' + esc(v.sku || '') + '</td><td>' + vp + '</td><td>' + qty + '</td></tr>';
      });
      html += '</tbody></table>';
    }

    // Tags
    const tags = p.tags || [];
    if (tags.length) {
      html += '<div class="detail-section-title">Tags</div>';
      html += '<div class="tag-list">' + tags.map(t => '<span class="tag">' + esc(t) + '</span>').join('') + '</div>';
    }

    // Image gallery
    if (images.length > 1) {
      html += '<div class="detail-section-title">Images</div>';
      html += '<div class="img-gallery">' + images.map(img =>
        '<img src="' + esc(img.url) + '" alt="' + esc(img.altText || '') + '" onclick="document.querySelector(\\'.detail-image\\').innerHTML=\\'<img src=\\\\\\"' + esc(img.url) + '\\\\\\" style=\\\\"width:100%;height:100%;object-fit:cover\\\\">\\'">'
      ).join('') + '</div>';
    }

    html += '</div>';
    content.innerHTML = html;
  }

  // ---- Carts ----

  async function loadCarts() {
    try {
      const data = await fetchJSON('/api/carts');
      const carts = data.carts || [];
      const badge = document.getElementById('cart-badge');
      if (carts.length) { badge.textContent = carts.length; badge.style.display = ''; }
      else badge.style.display = 'none';
      renderCarts(carts);
    } catch (e) {
      document.getElementById('carts-container').innerHTML = '<div class="empty"><div class="empty-icon">⚠️</div>Failed to load carts</div>';
    }
  }

  function renderCarts(carts) {
    const container = document.getElementById('carts-container');
    if (!carts.length) {
      container.innerHTML = '<div class="empty"><div class="empty-icon">🛒</div>No carts found</div>';
      return;
    }
    container.innerHTML = '<div class="carts-grid">' + carts.map(c => cartCardHTML(c)).join('') + '</div>';
  }

  function cartCardHTML(c) {
    const total = c.totalAmount ? formatPrice(c.totalAmount.amount, c.totalAmount.currencyCode) : '—';
    const active = c.id === selectedCartId ? ' active' : '';
    return '<div class="cart-card' + active + '" onclick="showCart(\\'' + esc(c.id) + '\\')">' +
      '<div class="cart-id">' + esc(c.id) + '</div>' +
      '<div class="cart-meta">' +
        '<div class="cart-stat"><div class="val">' + (c.itemCount || 0) + '</div><div class="lbl">Items</div></div>' +
        '<div class="cart-stat"><div class="val">' + (c.totalQuantity || 0) + '</div><div class="lbl">Qty</div></div>' +
      '</div>' +
      '<div class="cart-total">' + total + '</div>' +
    '</div>';
  }

  async function showCart(id) {
    selectedCartId = id;
    loadCarts(); // re-render to highlight
    const panel = document.getElementById('detail-panel');
    const content = document.getElementById('detail-content');
    document.getElementById('detail-panel-title').textContent = 'Cart';
    content.innerHTML = '<div class="empty">Loading...</div>';
    panel.classList.remove('hidden');

    try {
      const data = await fetchJSON('/api/carts/' + encodeURIComponent(id));
      renderCartDetail(data.cart);
    } catch (e) {
      content.innerHTML = '<div class="empty">Failed to load cart</div>';
    }
  }

  function renderCartDetail(cart) {
    const content = document.getElementById('detail-content');
    const total = cart.cost?.totalAmount ? formatPrice(cart.cost.totalAmount.amount, cart.cost.totalAmount.currencyCode) : '—';
    const lines = cart.lines || [];

    let html = '<div class="detail-body">';
    html += '<div class="cart-id" style="margin-bottom:12px">' + esc(cart.id) + '</div>';
    html += '<div class="detail-price-row"><span class="detail-price">' + total + '</span><span style="font-size:13px;color:#6d7175">' + (cart.totalQuantity || 0) + ' items</span></div>';

    if (cart.note) html += '<div style="font-size:13px;color:#4a4a4a;margin:8px 0;padding:8px;background:#f6f6f7;border-radius:4px">' + esc(cart.note) + '</div>';

    if (cart.checkoutUrl) html += '<div style="margin-bottom:12px"><a href="' + esc(cart.checkoutUrl) + '" target="_blank" style="font-size:12px;color:#008060">Checkout URL ↗</a></div>';

    if (lines.length) {
      html += '<div class="detail-section-title">Line Items</div>';
      html += '<div class="cart-lines">';
      lines.forEach(line => {
        const merch = line.merchandise || {};
        const product = merch.product || {};
        const img = merch.image;
        const thumbHTML = img ? '<img src="' + esc(img.url || img) + '" alt="">' : '🛍️';
        const lineTotal = line.cost?.totalAmount ? formatPrice(line.cost.totalAmount.amount, line.cost.totalAmount.currencyCode) : '';
        const opts = (merch.selectedOptions || []).map(o => o.name + ': ' + o.value).join(', ');
        html += '<div class="cart-line">' +
          '<div class="cart-line-thumb">' + thumbHTML + '</div>' +
          '<div class="cart-line-info">' +
            '<div class="cart-line-name">' + esc(product.title || merch.title || 'Item') + '</div>' +
            (merch.title && merch.title !== 'Default Title' ? '<div class="cart-line-variant">' + esc(merch.title) + '</div>' : '') +
            (opts ? '<div class="cart-line-variant">' + esc(opts) + '</div>' : '') +
            '<div class="cart-line-qty">Qty: ' + (line.quantity || 0) + '</div>' +
          '</div>' +
          '<div class="cart-line-price">' + lineTotal + '</div>' +
        '</div>';
      });
      html += '</div>';
    } else {
      html += '<div style="padding:20px 0;text-align:center;color:#8c9196;font-size:13px">Empty cart</div>';
    }

    html += '</div>';
    content.innerHTML = html;
  }

  // ---- Policies ----

  async function loadPolicies() {
    try {
      const data = await fetchJSON('/api/policies');
      renderPolicies(data.policies || []);
    } catch (e) {
      document.getElementById('policies-container').innerHTML = '<div class="empty"><div class="empty-icon">⚠️</div>Failed to load policies</div>';
    }
  }

  function renderPolicies(policies) {
    const container = document.getElementById('policies-container');
    if (!policies.length) {
      container.innerHTML = '<div class="empty"><div class="empty-icon">📄</div>No policies found</div>';
      return;
    }
    container.innerHTML = policies.map(p =>
      '<div class="policy-card">' +
        '<div class="policy-title">' + esc(p.title) + '</div>' +
        '<div class="policy-body">' + esc(p.body) + '</div>' +
      '</div>'
    ).join('');
  }

  // ---- View switching ----

  function showView(view) {
    currentView = view;
    closeDetail();

    document.getElementById('products-view').classList.toggle('hidden', view !== 'products');
    document.getElementById('carts-view').classList.toggle('hidden', view !== 'carts');
    document.getElementById('policies-view').classList.toggle('hidden', view !== 'policies');
    document.getElementById('sidebar').style.display = view === 'products' ? '' : 'none';

    document.getElementById('nav-products').classList.toggle('active', view === 'products');
    document.getElementById('nav-carts').classList.toggle('active', view === 'carts');
    document.getElementById('nav-policies').classList.toggle('active', view === 'policies');

    if (view === 'carts') loadCarts();
    if (view === 'policies') loadPolicies();
  }

  function closeDetail() {
    document.getElementById('detail-panel').classList.add('hidden');
    selectedProductId = null;
    selectedCartId = null;
  }

  function setView(mode) {
    viewMode = mode;
    document.getElementById('view-grid').classList.toggle('active', mode === 'grid');
    document.getElementById('view-list').classList.toggle('active', mode === 'list');
    renderProducts();
  }

  function onSearch() {
    clearTimeout(searchTimeout);
    searchTimeout = setTimeout(applyFilters, 250);
  }

  // ---- Init ----
  loadProducts();
  loadCarts(); // pre-load for badge count
</script>
</body>
</html>"""
