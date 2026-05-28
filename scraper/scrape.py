"""
Cloud Inmobiliaria — Scraper v3.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Estrategia (sin browser, 100% API/requests):

  1. MercadoLibre API pública  → api.mercadolibre.com (JSON, sin WAF)
  2. Inmuebles24 via requests  → __NEXT_DATA__ SSR JSON
  3. Lamudi.com.mx via requests → HTML parsing

GitHub Actions corre lunes–viernes 9AM UTC
"""

import json
import re
import sys
import time
import random
import requests as req

from datetime import datetime, timezone, timedelta
from pathlib import Path

# UTF-8 para emojis en logs
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, 'reconfigure'):
        stream.reconfigure(encoding='utf-8', errors='replace')

# ── Paths ─────────────────────────────────────────────────────────────────────
OUTPUT_FILE = Path(__file__).parent.parent / "data" / "listings.json"

# ── CDT timezone ──────────────────────────────────────────────────────────────
CDT = timezone(timedelta(hours=-5))

# ── Browser headers ───────────────────────────────────────────────────────────
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
HEADERS_WEB = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "es-MX,es;q=0.9,en-US;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Cache-Control": "max-age=0",
}
HEADERS_API = {
    "User-Agent": UA,
    "Accept": "application/json",
    "Accept-Language": "es-MX,es;q=0.9",
}

# ── Coordenadas por calle ─────────────────────────────────────────────────────
COORD_MAP = {
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
    "nubes residencial":   (20.6105, -103.3540),
    "terralta central":    (20.6095, -103.3556),
    "coto san gabriel":    (20.6130, -103.3570),
    "altosur":             (20.6075, -103.3492),
    "alto sur":            (20.6075, -103.3492),
    "terralta":            (20.6100, -103.3555),
}
CENTER = (20.6100, -103.3555)


def guess_coords(text: str):
    tl = text.lower()
    for key, coords in COORD_MAP.items():
        if key in tl:
            return coords
    return CENTER


def rand_sleep(a=0.5, b=1.5):
    time.sleep(random.uniform(a, b))


# ══════════════════════════════════════════════════════════════════════════════
# FUENTE 1: MercadoLibre API pública
# ══════════════════════════════════════════════════════════════════════════════
ML_API_BASE = "https://api.mercadolibre.com"

# Categorías de inmuebles en México (MLM = Mexico)
# MLM1459 = Inmuebles, sub-categorías:
ML_CATEGORIES = {
    "casas_venta":  "MLM1459",   # Inmuebles general
}

# IDs de estado/ciudad de MercadoLibre para Jalisco/Tlaquepaque
# Se obtienen de: GET /classified_locations/search?country_id=MX&q=tlaquepaque
ML_LOCATION_IDS = {
    # Buscar por query string es más fácil que filtrar por location ID
}


def ml_api_search(query: str, limit: int = 50, offset: int = 0) -> list[dict]:
    """
    Busca en MercadoLibre API pública.
    Docs: https://developers.mercadolibre.com.mx/es_ar/items-y-busquedas
    """
    url = f"{ML_API_BASE}/sites/MLM/search"
    params = {
        "q": query,
        "category": "MLM1459",  # Inmuebles México
        "limit": min(limit, 50),
        "offset": offset,
    }
    try:
        resp = req.get(url, params=params, headers=HEADERS_API, timeout=20)
        if resp.status_code == 200:
            return resp.json().get("results", [])
        print(f"    ML API HTTP {resp.status_code} para '{query}'")
        return []
    except Exception as e:
        print(f"    ML API error: {e}")
        return []


def ml_api_get_item(item_id: str) -> dict:
    """Obtiene detalles completos de un item (con atributos completos)"""
    try:
        resp = req.get(f"{ML_API_BASE}/items/{item_id}", headers=HEADERS_API, timeout=15)
        if resp.status_code == 200:
            return resp.json()
        return {}
    except Exception:
        return {}


def parse_ml_result(item: dict) -> dict | None:
    """Parsea un resultado de MercadoLibre API → nuestro formato"""
    precio = item.get("price") or item.get("original_price")
    if not precio:
        return None
    precio = int(float(precio))

    # Determinar modalidad por rango de precio o tipo de operación
    # En ML, las casas en renta se listan con precios mensuales (< 100k)
    # Las casas en venta con precios > 200k
    sale_conditions = item.get("sale_conditions", {})
    modalidad = "Renta"
    if precio >= 150_000:
        modalidad = "Venta"
    elif precio < 150_000:
        # Podría ser renta o venta de terreno barato. Checar condiciones.
        modalidad = "Renta"

    titulo = item.get("title", "Propiedad en Terralta")[:80]
    listing_id = str(item.get("id", ""))
    url = item.get("permalink", "")
    thumbnail = item.get("thumbnail", "")
    if thumbnail:
        # Subir resolución
        thumbnail = thumbnail.replace("-I.jpg", "-O.jpg").replace("http://", "https://")

    tipo_prop = "Depto" if "departamento" in titulo.lower() or "depto" in titulo.lower() else "Casa"
    remate = bool(re.search(r"remate|bancario|embargo", titulo.lower()))

    # Atributos (bedrooms, bathrooms, area)
    m2c = rec = ban = estac = None
    attrs = item.get("attributes", [])
    for attr in attrs:
        if not isinstance(attr, dict):
            continue
        attr_id = attr.get("id", "")
        val_name = attr.get("value_name") or ""
        try:
            val_num = float(re.sub(r"[^\d.]", "", str(val_name))) if val_name else 0
        except ValueError:
            val_num = 0

        if attr_id == "COVERED_AREA" or "covered_area" in attr_id.lower():
            m2c = int(val_num) if val_num else None
        elif attr_id == "TOTAL_AREA" and not m2c:
            m2c = int(val_num) if val_num else None
        elif attr_id == "BEDROOMS":
            rec = int(val_num) if val_num else None
        elif attr_id == "BATHROOMS":
            ban = int(val_num) if val_num else None
        elif attr_id == "PARKING_LOTS" or attr_id == "GARAGE":
            estac = int(val_num) if val_num else None

    # Dirección desde seller_address
    addr_obj = item.get("seller_address", {})
    city = addr_obj.get("city", {}).get("name", "") if isinstance(addr_obj, dict) else ""
    calle = city or "Terralta, San Pedro Tlaquepaque"
    lat, lng = guess_coords(titulo + " " + calle)

    return {
        "id": listing_id,
        "modalidad": modalidad,
        "tipoProp": tipo_prop,
        "titulo": titulo,
        "calle": calle,
        "precio": precio,
        "m2c": m2c,
        "rec": rec,
        "ban": ban,
        "estac": estac,
        "extras": "MercadoLibre",
        "remate": remate,
        "lat": lat,
        "lng": lng,
        "fuente": "MercadoLibre",
        "img": thumbnail or None,
        "url": url or None,
        "listing_id": listing_id,
    }


def scrape_mercadolibre() -> list[dict]:
    """Scrape completo de MercadoLibre via API pública"""
    print("  → MercadoLibre API pública...")

    queries = [
        "casa terralta san pedro tlaquepaque",
        "terralta fraccionamiento jalisco casa",
        "terralta tlaquepaque venta renta",
    ]

    all_items: list[dict] = []
    seen_ids: set[str] = set()

    for q in queries:
        rand_sleep(0.3, 0.8)
        results = ml_api_search(q, limit=50)
        for item in results:
            iid = str(item.get("id", ""))
            if iid and iid not in seen_ids:
                seen_ids.add(iid)
                all_items.append(item)

    if not all_items:
        print("  ✗ MercadoLibre API: 0 resultados")
        return []

    # Filtrar por relevancia — solo propiedades que mencionen Terralta o Tlaquepaque
    relevant = []
    for item in all_items:
        title = item.get("title", "").lower()
        addr = json.dumps(item.get("seller_address", {})).lower()
        if "terralta" in title or "terralta" in addr or "tlaquepaque" in title or "tlaquepaque" in addr:
            relevant.append(item)

    print(f"    {len(all_items)} resultados → {len(relevant)} relevantes para Terralta")

    listings = []
    for item in relevant:
        parsed = parse_ml_result(item)
        if parsed:
            listings.append(parsed)

    print(f"  ✓ {len(listings)} propiedades ML via API")
    return listings


# ══════════════════════════════════════════════════════════════════════════════
# FUENTE 2: Inmuebles24 via requests + __NEXT_DATA__
# ══════════════════════════════════════════════════════════════════════════════

I24_SEARCH_URLS = [
    "https://www.inmuebles24.com/inmuebles-en-fraccionamiento-terralta.html",
    "https://www.inmuebles24.com/casas-en-fraccionamiento-terralta-jalisco.html",
    "https://www.inmuebles24.com/inmuebles-en-san-pedro-tlaquepaque-jalisco.html?q=terralta",
]

# User-agents adicionales para rotación
UAS = [
    UA,
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
]


def make_session(ua: str | None = None) -> req.Session:
    s = req.Session()
    h = {**HEADERS_WEB}
    if ua:
        h["User-Agent"] = ua
    s.headers.update(h)
    return s


def fetch_html(url: str, session: req.Session = None, warm_up_url: str = None) -> str | None:
    """Fetch HTML con requests. Intenta con warm-up de cookies."""
    s = session or make_session(random.choice(UAS))
    try:
        if warm_up_url:
            rand_sleep(0.5, 1.5)
            s.get(warm_up_url, timeout=20, allow_redirects=True)
            rand_sleep(0.5, 1.5)
        resp = s.get(url, timeout=30, allow_redirects=True)
        if resp.status_code == 200:
            return resp.text
        print(f"    HTTP {resp.status_code}: {url[:80]}")
        return None
    except Exception as e:
        print(f"    fetch error: {e}")
        return None


def extract_next_data(html: str) -> dict | None:
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
    if not m:
        m = re.search(r'window\.__NEXT_DATA__\s*=\s*(\{.*?\})\s*;?\s*</script>', html, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception as e:
            print(f"    JSON parse error: {e}")
    return None


def find_postings(obj, depth=0) -> list | None:
    """Búsqueda recursiva de array de listings en __NEXT_DATA__"""
    if depth > 8:
        return None
    if isinstance(obj, list) and len(obj) >= 2 and isinstance(obj[0], dict):
        sample = obj[0]
        signals = ("postingUrl", "operationType", "priceOperationTypes", "realEstateType",
                   "publicationId", "listingType")
        if any(k in sample for k in signals):
            return obj
    if isinstance(obj, dict):
        for k in ("postings", "listings", "results", "items", "data", "searchResults"):
            if k in obj:
                r = find_postings(obj[k], depth + 1)
                if r:
                    return r
        for v in obj.values():
            r = find_postings(v, depth + 1)
            if r:
                return r
    return None


def parse_i24_posting(p: dict) -> dict | None:
    """Parsea un posting de Inmuebles24 __NEXT_DATA__"""
    precio = None
    for op in p.get("priceOperationTypes", []):
        for price_obj in op.get("prices", []):
            try:
                amt = float(price_obj.get("amount", 0))
                cur = price_obj.get("currency", "MXN")
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
    for _k, feat in p.get("mainFeatures", {}).items():
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
            elif any(x in name for x in ("bedroom", "dormitorio", "recamara", "recámara")):
                rec = v
            elif any(x in name for x in ("bathroom", "baño", "bano")):
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

    listing_id = str(p.get("id") or p.get("publicationId") or p.get("listingId") or "")
    url = p.get("postingUrl") or (
        f"https://www.inmuebles24.com/propiedades-{listing_id}.html" if listing_id else None
    )

    geo = p.get("geo", {})
    address = ""
    if isinstance(geo, dict):
        address = geo.get("address") or geo.get("street") or ""
    if not address:
        address = str(p.get("title", "Terralta"))[:60]

    remate = bool(re.search(r"remate|bancario", str(p).lower()))
    lat, lng = guess_coords(address)
    calle_short = address.split(",")[0].strip() or "Terralta"

    return {
        "id": listing_id,
        "modalidad": modalidad,
        "tipoProp": tipo_prop,
        "titulo": f"{'Casa' if tipo_prop == 'Casa' else 'Depto'} {'Remate' if remate else 'en ' + modalidad} — {calle_short}",
        "calle": address or "Terralta, San Pedro Tlaquepaque",
        "precio": precio,
        "m2c": m2c,
        "rec": rec,
        "ban": ban,
        "estac": estac,
        "extras": "",
        "remate": remate,
        "lat": lat,
        "lng": lng,
        "fuente": "Inmuebles24",
        "img": img,
        "url": url,
        "listing_id": listing_id,
    }


def scrape_inmuebles24() -> list[dict]:
    print("  → Inmuebles24 (requests + __NEXT_DATA__)...")
    for url in I24_SEARCH_URLS:
        html = fetch_html(url, warm_up_url="https://www.inmuebles24.com/")
        if not html:
            continue
        nd = extract_next_data(html)
        if nd:
            postings = find_postings(nd)
            if postings:
                listings = [parse_i24_posting(p) for p in postings]
                listings = [l for l in listings if l]
                if listings:
                    print(f"  ✓ {len(listings)} propiedades Inmuebles24")
                    return listings
        # Intentar siguiente URL
        rand_sleep(1, 2)

    print("  ✗ Inmuebles24: 0 propiedades (posible bloqueo por IP)")
    return []


# ══════════════════════════════════════════════════════════════════════════════
# FUENTE 3: Lamudi.com.mx via requests (OLX group — menos restrictivo)
# ══════════════════════════════════════════════════════════════════════════════

def scrape_lamudi() -> list[dict]:
    print("  → Lamudi.com.mx...")
    urls = [
        "https://www.lamudi.com.mx/jalisco/san-pedro-tlaquepaque/for-sale/?q=terralta",
        "https://www.lamudi.com.mx/jalisco/san-pedro-tlaquepaque/for-rent/?q=terralta",
    ]
    listings = []
    for url in urls:
        rand_sleep(0.5, 1)
        modalidad = "Renta" if "for-rent" in url else "Venta"
        html = fetch_html(url)
        if not html:
            continue

        # Lamudi expone JSON-LD y/o __INITIAL_STATE__
        # Buscar JSON-LD de listings
        ld_matches = re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.DOTALL)
        for ld_str in ld_matches:
            try:
                ld = json.loads(ld_str)
                if isinstance(ld, list):
                    for item in ld:
                        l = parse_lamudi_item(item, modalidad)
                        if l:
                            listings.append(l)
                elif isinstance(ld, dict):
                    l = parse_lamudi_item(ld, modalidad)
                    if l:
                        listings.append(l)
            except Exception:
                pass

        # Buscar __INITIAL_STATE__ o similar
        state_m = re.search(r'window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*;?\s*</script>', html, re.DOTALL)
        if state_m:
            try:
                state = json.loads(state_m.group(1))
                postings = find_postings(state)
                if postings:
                    for p in postings:
                        l = parse_lamudi_item(p, modalidad)
                        if l:
                            listings.append(l)
            except Exception:
                pass

    if listings:
        print(f"  ✓ {len(listings)} propiedades Lamudi")
    else:
        print("  ✗ Lamudi: 0 propiedades")
    return listings


def parse_lamudi_item(item: dict, modalidad: str) -> dict | None:
    """Parsea item de Lamudi"""
    if not isinstance(item, dict):
        return None

    # Precio
    precio = None
    for key in ("price", "lowPrice", "amount", "offers"):
        val = item.get(key)
        if isinstance(val, dict):
            val = val.get("price") or val.get("lowPrice")
        if val:
            try:
                precio = int(float(str(val).replace(",", "").replace("$", "")))
                break
            except Exception:
                pass
    if not precio:
        return None

    titulo = str(item.get("name", "") or item.get("title", "Propiedad en Terralta"))[:80]
    listing_id = str(item.get("identifier", {}).get("value", "") if isinstance(item.get("identifier"), dict) else item.get("id", ""))
    url = item.get("url") or item.get("@id") or ""

    tipo_prop = "Depto" if "departamento" in titulo.lower() or "depto" in titulo.lower() else "Casa"
    remate = bool(re.search(r"remate|bancario", titulo.lower()))

    # Dirección
    address_obj = item.get("address", {})
    address = ""
    if isinstance(address_obj, dict):
        address = address_obj.get("streetAddress", "") or address_obj.get("addressLocality", "")
    if not address:
        address = "Terralta, San Pedro Tlaquepaque"

    lat, lng = guess_coords(titulo + " " + address)

    # Imagen
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
        "id": listing_id or f"lamudi-{hash(titulo) % 100000}",
        "modalidad": modalidad,
        "tipoProp": tipo_prop,
        "titulo": titulo,
        "calle": address,
        "precio": precio,
        "m2c": None,
        "rec": None,
        "ban": None,
        "estac": None,
        "extras": "",
        "remate": remate,
        "lat": lat,
        "lng": lng,
        "fuente": "Lamudi",
        "img": img,
        "url": url if url.startswith("http") else None,
        "listing_id": listing_id,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Utilidades
# ══════════════════════════════════════════════════════════════════════════════

def dedup(listings: list[dict]) -> list[dict]:
    seen = set()
    unique = []
    for p in listings:
        key = (p.get("precio"), round(p.get("lat", 0), 3), round(p.get("lng", 0), 3))
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique


def assign_ids(listings: list[dict]) -> list[dict]:
    for i, p in enumerate(listings, start=1):
        p["id"] = i
    return listings


def load_desarrollos() -> list[dict]:
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
    print("\n🏠 Cloud Inmobiliaria · Scraper v3.0")
    print(f"   Zona: Fraccionamiento Terralta, San Pedro Tlaquepaque")
    print(f"   Hora: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    all_listings: list[dict] = []

    # 1. MercadoLibre API pública (más confiable)
    try:
        ml = scrape_mercadolibre()
        all_listings.extend(ml)
    except Exception as e:
        print(f"  ✗ ML fatal: {e}")

    # 2. Inmuebles24
    try:
        i24 = scrape_inmuebles24()
        all_listings.extend(i24)
    except Exception as e:
        print(f"  ✗ I24 fatal: {e}")

    # 3. Lamudi
    try:
        lam = scrape_lamudi()
        all_listings.extend(lam)
    except Exception as e:
        print(f"  ✗ Lamudi fatal: {e}")

    # Limpiar
    all_listings = [p for p in all_listings if p.get("precio") and p["precio"] > 0]
    all_listings = dedup(all_listings)

    print(f"\n  Total bruto: {len(all_listings)} propiedades")

    # Guard: nunca sobrescribir con 0
    if len(all_listings) == 0:
        print("\n⚠️  Scraping = 0 propiedades. Archivo NO modificado.")
        print("   Causa probable: IP de GitHub Actions bloqueada por portales.")
        sys.exit(0)

    all_listings = assign_ids(all_listings)
    all_listings.sort(key=lambda p: (p.get("modalidad") == "Renta", p.get("precio", 0)))

    desarrollos = load_desarrollos()
    fuentes = sorted({p["fuente"] for p in all_listings})
    ts = datetime.now(CDT).isoformat(timespec="seconds")

    out = {
        "_meta": {
            "actualizado": ts,
            "fuentes": fuentes,
            "zona": "Fraccionamiento Terralta, San Pedro Tlaquepaque",
            "total": len(all_listings),
            "scraper_version": "3.0",
        },
        "propiedades": all_listings,
        "desarrollos": desarrollos,
    }

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(out, ensure_ascii=False, indent=2), "utf-8")

    print(f"\n✅ {len(all_listings)} propiedades → {OUTPUT_FILE}")
    print(f"   Fuentes: {', '.join(fuentes)}")
    print(f"   Actualizado: {ts}\n")


if __name__ == "__main__":
    main()
