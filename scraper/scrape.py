"""
Cloud Inmobiliaria — Scraper v5.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Fuentes:
  1. MercadoLibre API OAuth (autenticada, pasa WAF) ← PRINCIPAL
  2. Inmuebles24 requests directo (funciona desde IP residencial/local)
  3. Lamudi.com.mx requests directo

Configuración requerida en GitHub Secrets:
  ML_APP_ID     → App ID de developers.mercadolibre.com.mx
  ML_APP_SECRET → App Secret de developers.mercadolibre.com.mx

Registro gratuito: https://developers.mercadolibre.com.mx/
"""

import json
import re
import sys
import time
import random
import os

from datetime import datetime, timezone, timedelta
from pathlib import Path

for s in (sys.stdout, sys.stderr):
    if hasattr(s, 'reconfigure'):
        s.reconfigure(encoding='utf-8', errors='replace')

# ── Importar curl_cffi o requests ─────────────────────────────────────────────
try:
    from curl_cffi import requests as cfreq
    IMPERSONATE = "chrome131"
    HAS_CURL = True
except ImportError:
    import requests as cfreq
    IMPERSONATE = None
    HAS_CURL = False

import requests as _req  # siempre disponible para OAuth (no necesita impersonación)

# ── Config ────────────────────────────────────────────────────────────────────
OUTPUT_FILE = Path(__file__).parent.parent / "data" / "listings.json"
CDT = timezone(timedelta(hours=-5))

ML_APP_ID     = os.environ.get("ML_APP_ID", "")
ML_APP_SECRET = os.environ.get("ML_APP_SECRET", "")

# ── Proxy residencial IPRoyal — bypass bloqueo IPs datacenter GH Actions ──────
PROXY_URL = os.environ.get("PROXY_URL", "")
PROXIES = {"http": PROXY_URL, "https": PROXY_URL} if PROXY_URL else {}
if PROXY_URL:
    print(f"   Proxy residencial: configurado ({PROXY_URL.split('@')[-1] if '@' in PROXY_URL else 'OK'})")
else:
    print("   Proxy residencial: NO configurado (algunos portales pueden bloquear)")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

HEADERS_WEB = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "es-MX,es;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "max-age=0",
}

# ── Coordenadas ───────────────────────────────────────────────────────────────
COORDS = {
    "tierra tranquila":    (20.6100, -103.3545),
    "tierra encantada":    (20.6108, -103.3538),
    "tierra contenta":     (20.6120, -103.3525),
    "tierra hermosa":      (20.6115, -103.3542),
    "tierra de unicornios":(20.6128, -103.3572),
    "tierra de ilusiones": (20.6118, -103.3565),
    "tierra del honor":    (20.6102, -103.3550),
    "tierra de honor":     (20.6100, -103.3548),
    "tierra de esperanza": (20.6098, -103.3554),
    "tierra de danza":     (20.6110, -103.3565),
    "tierra de gloria":    (20.6103, -103.3548),
    "las nubes":           (20.6105, -103.3540),
    "terralta central":    (20.6095, -103.3556),
    "coto san gabriel":    (20.6130, -103.3570),
    "altosur":             (20.6075, -103.3492),
    "terralta":            (20.6100, -103.3555),
}
CENTER = (20.6100, -103.3555)


def coords(text: str):
    tl = text.lower()
    for k, v in COORDS.items():
        if k in tl:
            return v
    return CENTER


def jitter(a=0.3, b=1.2):
    time.sleep(random.uniform(a, b))


# ── HTTP helpers ──────────────────────────────────────────────────────────────
def make_web_session():
    if HAS_CURL:
        s = cfreq.Session(impersonate=IMPERSONATE)
        if PROXY_URL:
            s.proxies = PROXIES
        return s
    s = cfreq.Session()
    s.headers.update(HEADERS_WEB)
    if PROXY_URL:
        s.proxies = PROXIES
    return s


def http_get(url: str, session=None, headers: dict = None, timeout=30) -> tuple[int, str | None]:
    """
    Si PROXY_URL está configurado, usa requests estándar (compatible con HTTP proxies).
    Si no hay proxy, usa curl_cffi con Chrome impersonation (bypass WAF sin proxy).
    """
    h = {**HEADERS_WEB, **(headers or {})}
    try:
        if PROXY_URL:
            # curl_cffi tiene incompatibilidad de TLS con proxies HTTP — usar requests
            import requests as stdreq
            resp = stdreq.get(url, headers=h, proxies=PROXIES,
                              timeout=timeout, allow_redirects=True)
        elif HAS_CURL:
            s = session or make_web_session()
            resp = s.get(url, headers=headers or {}, timeout=timeout, allow_redirects=True)
        else:
            s = session or make_web_session()
            resp = s.get(url, headers=h, timeout=timeout, allow_redirects=True)
        return resp.status_code, resp.text
    except Exception as e:
        print(f"    GET error: {e}")
        return 0, None


# ══════════════════════════════════════════════════════════════════════════════
# FUENTE 1: MercadoLibre OAuth API
# ══════════════════════════════════════════════════════════════════════════════

def ml_get_token() -> str | None:
    """
    Obtiene access_token de ML via client_credentials.
    Requiere ML_APP_ID y ML_APP_SECRET como env vars / GitHub Secrets.
    Registro gratis: https://developers.mercadolibre.com.mx/
    """
    if not ML_APP_ID or not ML_APP_SECRET:
        print("    ML OAuth: ML_APP_ID / ML_APP_SECRET no configurados")
        print("    → Registra app en https://developers.mercadolibre.com.mx/")
        print("    → Agrega ML_APP_ID y ML_APP_SECRET como GitHub Secrets")
        return None

    try:
        resp = _req.post(
            "https://api.mercadolibre.com/oauth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": ML_APP_ID,
                "client_secret": ML_APP_SECRET,
            },
            headers={"Accept": "application/json"},
            timeout=20,
        )
        if resp.status_code == 200:
            token = resp.json().get("access_token")
            print(f"    ML OAuth: token obtenido ✓")
            return token
        print(f"    ML OAuth error {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        print(f"    ML OAuth exception: {e}")
    return None


def ml_search(token: str, query: str, limit: int = 50) -> list[dict]:
    try:
        resp = _req.get(
            "https://api.mercadolibre.com/sites/MLM/search",
            params={"q": query, "limit": limit},
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=25,
        )
        if resp.status_code == 200:
            return resp.json().get("results", [])
        print(f"    ML search HTTP {resp.status_code} para '{query}'")
    except Exception as e:
        print(f"    ML search error: {e}")
    return []


def scrape_mercadolibre() -> list[dict]:
    print("  → MercadoLibre API (OAuth)...")
    token = ml_get_token()
    if not token:
        # Intentar sin auth (funciona desde IPs residenciales)
        print("    Intentando sin autenticación (solo funciona desde IP residencial)...")
        items = []
        for q in ["terralta san pedro tlaquepaque", "terralta fraccionamiento jalisco"]:
            jitter(0.5, 1.5)
            try:
                resp = _req.get(
                    "https://api.mercadolibre.com/sites/MLM/search",
                    params={"q": q, "limit": 50},
                    headers={"User-Agent": UA, "Accept": "application/json"},
                    timeout=20,
                )
                if resp.status_code == 200:
                    items.extend(resp.json().get("results", []))
                else:
                    print(f"    ML sin auth HTTP {resp.status_code}")
            except Exception as e:
                print(f"    ML sin auth error: {e}")
        if not items:
            print("  ✗ MercadoLibre: 0 resultados (sin OAuth)")
            return []
        all_items = items
    else:
        all_items = []
        seen = set()
        for q in [
            "casa terralta san pedro tlaquepaque",
            "terralta fraccionamiento jalisco",
            "casa venta terralta jalisco",
        ]:
            jitter(0.3, 0.8)
            for item in ml_search(token, q):
                iid = str(item.get("id", ""))
                if iid and iid not in seen:
                    seen.add(iid)
                    all_items.append(item)

    # Filtrar por relevancia (Terralta / Tlaquepaque)
    relevant = []
    for item in all_items:
        text = (item.get("title", "") + json.dumps(item.get("seller_address", {}))).lower()
        if "terralta" in text or "tlaquepaque" in text:
            relevant.append(item)

    print(f"    {len(all_items)} resultados → {len(relevant)} en zona Terralta")
    listings = [l for l in (parse_ml(i) for i in relevant) if l]
    print(f"  ✓ {len(listings)} propiedades MercadoLibre")
    return listings


def parse_ml(item: dict) -> dict | None:
    precio = item.get("price") or item.get("original_price")
    if not precio:
        return None
    precio = int(float(precio))
    if precio <= 0:
        return None

    modalidad = "Renta" if precio < 150_000 else "Venta"
    titulo = (item.get("title") or "Propiedad en Terralta")[:80]
    listing_id = str(item.get("id", ""))
    url = item.get("permalink", "")
    thumbnail = (item.get("thumbnail") or "").replace("-I.jpg", "-O.jpg").replace("http://", "https://")

    tipo_prop = "Depto" if any(x in titulo.lower() for x in ("departamento", "depto")) else "Casa"
    remate = bool(re.search(r"remate|bancario|embargo", titulo.lower()))

    m2c = rec = ban = estac = None
    for attr in item.get("attributes", []):
        if not isinstance(attr, dict):
            continue
        aid = attr.get("id", "")
        vn = attr.get("value_name") or ""
        try:
            v = int(float(re.sub(r"[^\d.]", "", str(vn)))) if vn else 0
        except ValueError:
            v = 0
        if aid == "COVERED_AREA" and v:
            m2c = v
        elif aid == "TOTAL_AREA" and not m2c and v:
            m2c = v
        elif aid == "BEDROOMS" and v:
            rec = v
        elif aid == "BATHROOMS" and v:
            ban = v
        elif aid in ("PARKING_LOTS", "GARAGE") and v:
            estac = v

    addr = item.get("seller_address", {})
    city = addr.get("city", {}).get("name", "") if isinstance(addr, dict) else ""
    calle = city or "Terralta, San Pedro Tlaquepaque"
    lat, lng = coords(titulo + " " + calle)

    return {
        "id": listing_id, "modalidad": modalidad, "tipoProp": tipo_prop,
        "titulo": titulo, "calle": calle, "precio": precio,
        "m2c": m2c, "rec": rec, "ban": ban, "estac": estac,
        "extras": "", "remate": remate, "lat": lat, "lng": lng,
        "fuente": "MercadoLibre", "img": thumbnail or None,
        "url": url or None, "listing_id": listing_id,
    }


# ══════════════════════════════════════════════════════════════════════════════
# FUENTE 2: Inmuebles24 (requests — funciona desde IP residencial)
# ══════════════════════════════════════════════════════════════════════════════

I24_URLS = [
    "https://www.inmuebles24.com/inmuebles-en-fraccionamiento-terralta.html",
    "https://www.inmuebles24.com/casas-en-fraccionamiento-terralta-jalisco.html",
    "https://www.inmuebles24.com/inmuebles-en-san-pedro-tlaquepaque-jalisco.html?q=terralta",
]


def get_next_data(html: str) -> dict | None:
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
    if not m:
        m = re.search(r'window\.__NEXT_DATA__\s*=\s*(\{.*?\})\s*;?\s*</script>', html, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    return None


def find_postings(obj, depth=0) -> list | None:
    if depth > 8:
        return None
    if isinstance(obj, list) and len(obj) >= 2 and isinstance(obj[0], dict):
        if any(k in obj[0] for k in ("postingUrl", "operationType", "priceOperationTypes", "publicationId")):
            return obj
    if isinstance(obj, dict):
        for k in ("postings", "listings", "results", "items", "data"):
            if k in obj:
                r = find_postings(obj[k], depth + 1)
                if r:
                    return r
        for v in obj.values():
            r = find_postings(v, depth + 1)
            if r:
                return r
    return None


def parse_i24(p: dict) -> dict | None:
    precio = None
    for op in p.get("priceOperationTypes", []):
        for po in op.get("prices", []):
            try:
                amt = float(po.get("amount", 0))
                cur = po.get("currency", "MXN")
                if amt:
                    precio = int(amt * (17.5 if cur == "USD" else 1))
                    break
            except Exception:
                pass
        if precio:
            break
    if not precio:
        return None

    op_raw = str(p.get("operationType", "")).lower()
    modalidad = "Renta" if "alquiler" in op_raw or "renta" in op_raw else "Venta"
    tipo_raw = p.get("realEstateType", {})
    tipo_name = tipo_raw.get("name", "Casa") if isinstance(tipo_raw, dict) else str(tipo_raw)
    tipo_prop = "Depto" if "departamento" in tipo_name.lower() else "Casa"

    m2c = rec = ban = estac = None
    for feat in p.get("mainFeatures", {}).values():
        if not isinstance(feat, dict):
            continue
        name = feat.get("name", "").lower()
        val = feat.get("value")
        if val is None:
            continue
        try:
            v = int(float(val))
            if any(x in name for x in ("covered", "cubierta", "construida")):
                m2c = v
            elif any(x in name for x in ("bedroom", "dormitorio", "recamara")):
                rec = v
            elif any(x in name for x in ("bathroom", "baño")):
                ban = v
            elif any(x in name for x in ("parking", "estacionamiento", "cochera")):
                estac = v
        except (ValueError, TypeError):
            pass

    img = None
    try:
        photos = p.get("media", {}).get("photos", [])
        if photos:
            raw = photos[0].get("url", "") or photos[0].get("src", "")
            img = raw.replace("360x266", "720x532").split("?")[0] if raw else None
    except Exception:
        pass

    listing_id = str(p.get("id") or p.get("publicationId") or "")
    url = p.get("postingUrl") or (
        f"https://www.inmuebles24.com/propiedades-{listing_id}.html" if listing_id else None
    )
    geo = p.get("geo", {})
    address = (geo.get("address") or geo.get("street") or "") if isinstance(geo, dict) else ""
    if not address:
        address = str(p.get("title", "Terralta"))[:60]

    remate = bool(re.search(r"remate|bancario", str(p).lower()))
    lat, lng = coords(address)
    calle_short = address.split(",")[0].strip() or "Terralta"

    return {
        "id": listing_id, "modalidad": modalidad, "tipoProp": tipo_prop,
        "titulo": f"{'Casa' if tipo_prop == 'Casa' else 'Depto'} {'Remate' if remate else 'en ' + modalidad} — {calle_short}",
        "calle": address or "Terralta, San Pedro Tlaquepaque", "precio": precio,
        "m2c": m2c, "rec": rec, "ban": ban, "estac": estac,
        "extras": "", "remate": remate, "lat": lat, "lng": lng,
        "fuente": "Inmuebles24", "img": img, "url": url, "listing_id": listing_id,
    }


def _fetch_with_playwright(url: str) -> str | None:
    """Usa Playwright (Chrome headless real) para bypasear Cloudflare."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox",
                      "--disable-blink-features=AutomationControlled"]
            )
            ctx = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/131.0.0.0 Safari/537.36",
                locale="es-MX",
                extra_http_headers={"Accept-Language": "es-MX,es;q=0.9"},
            )
            page = ctx.new_page()
            # Eliminar señales de automatización
            page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3]});
            """)
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            # Esperar a que Cloudflare pase el challenge (hasta 8s)
            for _ in range(16):
                if "just a moment" not in page.title().lower():
                    break
                page.wait_for_timeout(500)
            html = page.content()
            browser.close()
            return html
    except Exception as e:
        print(f"    Playwright error: {e}")
        return None


def scrape_inmuebles24() -> list[dict]:
    print("  → Inmuebles24 (Playwright + fallback HTTP)...")

    for url in I24_URLS:
        html = None

        # 1er intento: Playwright (bypasea Cloudflare con Chrome real)
        html = _fetch_with_playwright(url)
        if html and "just a moment" in html.lower():
            print(f"    Playwright: Cloudflare no resuelto en {url[:60]}")
            html = None

        # 2do intento: HTTP directo (funciona desde IP residencial / local)
        if not html:
            status, html = http_get(url)
            if status != 200 or not html:
                print(f"    HTTP {status}: {url[:80]}")
                jitter(1, 2)
                continue
            if "just a moment" in html.lower():
                print(f"    HTTP bloqueado por Cloudflare: {url[:60]}")
                jitter(1, 2)
                continue

        nd = get_next_data(html)
        if nd:
            postings = find_postings(nd)
            if postings:
                listings = [parse_i24(p) for p in postings]
                listings = [l for l in listings if l]
                if listings:
                    print(f"  ✓ {len(listings)} propiedades Inmuebles24")
                    return listings
        jitter(1, 2)

    print("  ✗ Inmuebles24: 0 propiedades (Cloudflare bloqueó aun con Playwright)")
    return []


# ══════════════════════════════════════════════════════════════════════════════
# FUENTE 3: Facebook Marketplace (watchlist pública — OG meta tags, sin login)
# ══════════════════════════════════════════════════════════════════════════════

FB_WATCHLIST = Path(__file__).parent.parent / "data" / "fb_watchlist.json"

def scrape_facebook() -> list[dict]:
    """
    Lee data/fb_watchlist.json, visita cada listing de FB y extrae
    og:title / og:description / og:image sin necesitar login.
    Para agregar nuevos anuncios: edita fb_watchlist.json con el ID del URL.
    """
    print("  → Facebook Marketplace (watchlist)...")
    if not FB_WATCHLIST.exists():
        print("    ✗ fb_watchlist.json no encontrado")
        return []

    try:
        wl = json.loads(FB_WATCHLIST.read_text("utf-8"))
        items = wl.get("items", [])
    except Exception as e:
        print(f"    ✗ Error leyendo watchlist: {e}")
        return []

    if not items:
        return []

    session = make_web_session()
    listings = []

    for item in items:
        fb_id = str(item.get("id", "")).strip()
        if not fb_id:
            continue

        url = f"https://www.facebook.com/marketplace/item/{fb_id}/"
        jitter(1.5, 3.0)

        status, html = http_get(url, session=session, headers={
            "Accept-Language": "es-MX,es;q=0.9",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
        })

        if status != 200 or not html:
            print(f"    ✗ FB {fb_id}: HTTP {status}")
            continue

        # Extraer OG meta tags
        def og(prop):
            m = re.search(rf'<meta[^>]+property=["\']og:{prop}["\'][^>]+content=["\']([^"\']+)["\']', html)
            if not m:
                m = re.search(rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:{prop}["\']', html)
            return m.group(1).strip() if m else ""

        title = og("title") or item.get("nota", f"Propiedad FB {fb_id}")
        description = og("description")
        image = og("image")

        # Limpiar título (FB añade " | Facebook Marketplace" etc.)
        title = re.sub(r'\s*[|\-–]\s*Facebook.*$', '', title, flags=re.IGNORECASE).strip()
        if not title:
            title = item.get("nota", f"Propiedad Terralta — FB {fb_id}")

        # Extraer precio desde description o título
        precio = None
        for text in (description, title):
            m = re.search(r'MX\$\s*([\d,\.]+)|(\$\s*[\d,\.]+)', text)
            if m:
                raw = (m.group(1) or m.group(2)).replace('$','').replace(',','').replace(' ','')
                try:
                    v = float(raw)
                    if v > 1000:
                        precio = int(v)
                        break
                except Exception:
                    pass

        if not precio:
            # Usar precio de la watchlist si tiene campo "precio"
            precio = item.get("precio", 0)

        if not precio:
            print(f"    ✗ FB {fb_id}: sin precio detectado — skip")
            continue

        # Modalidad y remate desde watchlist o descripción
        modalidad = item.get("modalidad", "Venta")
        if "renta" in description.lower() or "alquiler" in description.lower():
            modalidad = "Renta"
        remate = item.get("remate", False)
        if "remate" in title.lower() or "remate" in description.lower():
            remate = True

        # Tipo de propiedad
        tipo = "Depto" if any(x in title.lower() for x in ("depto", "departamento", "apartamento")) else "Casa"

        # Coordenadas
        lat, lng = coords(title + " " + description)

        listing = {
            "titulo": title[:80],
            "calle": "Fraccionamiento Terralta, San Pedro Tlaquepaque",
            "precio": precio,
            "modalidad": modalidad,
            "tipoProp": tipo,
            "remate": remate,
            "fuente": "Facebook",
            "url": url,
            "lat": lat,
            "lng": lng,
            "img": image if image and "scontent" in image else None,
            "extras": f"Facebook Marketplace · {item.get('nota', '')}".strip(" ·"),
            "m2c": None, "rec": None, "ban": None, "estac": None,
        }
        listings.append(listing)
        print(f"    ✓ FB {fb_id}: {title[:45]} — ${precio:,}")

    if listings:
        print(f"  ✓ {len(listings)} propiedades Facebook Marketplace")
    else:
        print("  ✗ Facebook: 0 propiedades (IPs de GH Actions bloqueadas — normal)")
    return listings


# ══════════════════════════════════════════════════════════════════════════════
# FUENTE 4: Lamudi.com.mx
# ══════════════════════════════════════════════════════════════════════════════

def scrape_lamudi() -> list[dict]:
    print("  → Lamudi.com.mx...")
    session = make_web_session()
    listings = []

    for modalidad, url in [
        ("Venta", "https://www.lamudi.com.mx/jalisco/san-pedro-tlaquepaque/?search=terralta"),
        ("Renta", "https://www.lamudi.com.mx/jalisco/san-pedro-tlaquepaque/for-rent/?search=terralta"),
    ]:
        jitter(0.5, 1.0)
        status, html = http_get(url, session=session)
        if status != 200 or not html:
            print(f"    Lamudi HTTP {status} ({modalidad})")
            continue

        for ld_str in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.DOTALL):
            try:
                ld = json.loads(ld_str)
                items = ld if isinstance(ld, list) else [ld]
                for item in items:
                    l = parse_lamudi(item, modalidad)
                    if l:
                        listings.append(l)
            except Exception:
                pass

    if listings:
        print(f"  ✓ {len(listings)} propiedades Lamudi")
    else:
        print("  ✗ Lamudi: 0 propiedades")
    return listings


def parse_lamudi(item: dict, modalidad: str) -> dict | None:
    if not isinstance(item, dict):
        return None
    precio = None
    for key in ("price", "lowPrice", "amount"):
        val = item.get(key)
        if isinstance(val, dict):
            val = val.get("price") or val.get("lowPrice")
        if val:
            try:
                precio = int(float(str(val).replace(",", "").replace("$", "")))
                break
            except Exception:
                pass
    if not precio or precio < 1000:
        return None

    titulo = str(item.get("name", "") or item.get("title", "Propiedad en Terralta"))[:80]
    listing_id = str(item.get("id", hash(titulo) % 100000))
    url = item.get("url") or item.get("@id") or ""
    tipo_prop = "Depto" if "departamento" in titulo.lower() else "Casa"
    remate = bool(re.search(r"remate|bancario", titulo.lower()))
    addr_obj = item.get("address", {})
    address = (
        (addr_obj.get("streetAddress") or addr_obj.get("addressLocality") or "")
        if isinstance(addr_obj, dict) else ""
    ) or "Terralta, San Pedro Tlaquepaque"
    lat, lng = coords(titulo + " " + address)

    img = None
    for key in ("image", "photo", "thumbnail"):
        raw = item.get(key)
        if isinstance(raw, list) and raw:
            raw = raw[0]
        if isinstance(raw, dict):
            raw = raw.get("url") or raw.get("contentUrl")
        if isinstance(raw, str) and raw.startswith("http"):
            img = raw
            break

    return {
        "id": listing_id, "modalidad": modalidad, "tipoProp": tipo_prop,
        "titulo": titulo, "calle": address, "precio": precio,
        "m2c": None, "rec": None, "ban": None, "estac": None,
        "extras": "", "remate": remate, "lat": lat, "lng": lng,
        "fuente": "Lamudi", "img": img,
        "url": url if url.startswith("http") else None, "listing_id": listing_id,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Utilidades
# ══════════════════════════════════════════════════════════════════════════════

def dedup(listings):
    seen = set()
    out = []
    for p in listings:
        key = (p.get("precio"), round(p.get("lat", 0), 3), round(p.get("lng", 0), 3))
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def assign_ids(listings):
    for i, p in enumerate(listings, start=1):
        p["id"] = i
    return listings


def load_desarrollos():
    if OUTPUT_FILE.exists():
        try:
            return json.loads(OUTPUT_FILE.read_text("utf-8")).get("desarrollos", [])
        except Exception:
            pass
    return []


# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════
def main():
    print("\n🏠 Cloud Inmobiliaria · Scraper v6.0")
    print(f"   Zona: Fraccionamiento Terralta, San Pedro Tlaquepaque")
    print(f"   Hora: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"   ML OAuth: {'configurado ✓' if ML_APP_ID else 'no configurado (agregar ML_APP_ID y ML_APP_SECRET)'}")
    print(f"   curl_cffi: {'✓' if HAS_CURL else '✗ fallback requests'}\n")

    all_listings = []

    for fn in (scrape_mercadolibre, scrape_inmuebles24, scrape_lamudi, scrape_facebook):
        try:
            all_listings.extend(fn())
        except Exception as e:
            print(f"  ✗ {fn.__name__} fatal: {e}")

    all_listings = [p for p in all_listings if p.get("precio", 0) > 0]
    all_listings = dedup(all_listings)

    print(f"\n  Total: {len(all_listings)} propiedades")

    ts = datetime.now(CDT).isoformat(timespec="seconds")

    if len(all_listings) == 0:
        # IPs de GitHub Actions son bloqueadas por los portales.
        # Actualizamos SOLO el timestamp para que el badge diga "revisado hoy"
        # y mantenemos las propiedades del scrape anterior.
        print("\n⚠️  0 propiedades scraped (IPs bloqueadas desde datacenter).")
        print("   Actualizando timestamp para que el portal muestre 'revisado hoy'...")
        if OUTPUT_FILE.exists():
            try:
                prev = json.loads(OUTPUT_FILE.read_text("utf-8"))
                prev["_meta"]["actualizado"] = ts
                prev["_meta"]["ultima_revision"] = ts
                OUTPUT_FILE.write_text(json.dumps(prev, ensure_ascii=False, indent=2), "utf-8")
                print(f"   Timestamp actualizado: {ts}")
                print(f"   Propiedades conservadas: {prev['_meta']['total']}")
            except Exception as e:
                print(f"   Error actualizando timestamp: {e}")
        return

    all_listings = assign_ids(all_listings)
    all_listings.sort(key=lambda p: (p.get("modalidad") == "Renta", p.get("precio", 0)))

    fuentes = sorted({p["fuente"] for p in all_listings})

    out = {
        "_meta": {
            "actualizado": ts,
            "ultima_revision": ts,
            "fuentes": fuentes,
            "zona": "Fraccionamiento Terralta, San Pedro Tlaquepaque",
            "total": len(all_listings),
            "scraper_version": "6.0",
        },
        "propiedades": all_listings,
        "desarrollos": load_desarrollos(),
    }

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(out, ensure_ascii=False, indent=2), "utf-8")

    print(f"\n✅ {len(all_listings)} propiedades → {OUTPUT_FILE}")
    print(f"   Fuentes: {', '.join(fuentes)}")
    print(f"   Actualizado: {ts}\n")

    # Verificar que los links directos siguen vivos — eliminar inactivos
    try:
        import verify_links
        print("\n🔗 Verificando links de propiedades...")
        verify_links.main()
    except Exception as e:
        print(f"⚠️ No se pudieron verificar links: {e}")

    # Localizar imágenes: descargar copias estáticas para que TODOS vean lo mismo
    try:
        import localize_images
        print("📸 Localizando imágenes (snapshot estático)...")
        localize_images.main()
    except Exception as e:
        print(f"⚠️ No se pudieron localizar imágenes: {e}")


if __name__ == "__main__":
    main()
