"""
Cloud Inmobiliaria — Scraper v2.1
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Estrategia por portal:

  Inmuebles24 → requests + __NEXT_DATA__ (SSR JSON, sin browser)
               Fallback: Playwright + stealth si requests falla

  MercadoLibre → Playwright + stealth (JS-rendered)

Corre vía GitHub Actions cada 24h (lunes–viernes 9AM UTC)
"""

import asyncio
import json
import re
import sys
import os
import time
import random

# Forzar UTF-8 en stdout para Windows
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests as req

try:
    from playwright.async_api import async_playwright
except ImportError:
    print("ERROR: playwright no instalado.")
    sys.exit(1)

try:
    from playwright_stealth import stealth_async
    HAS_STEALTH = True
except ImportError:
    HAS_STEALTH = False

# ── Paths ─────────────────────────────────────────────────────────────────────
OUTPUT_FILE = Path(__file__).parent.parent / "data" / "listings.json"

# ── Headers de browser real ───────────────────────────────────────────────────
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "es-MX,es;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-CH-UA": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "Sec-CH-UA-Mobile": "?0",
    "Sec-CH-UA-Platform": '"Windows"',
    "Cache-Control": "max-age=0",
}

# ── URLs de búsqueda ──────────────────────────────────────────────────────────
I24_URLS = [
    "https://www.inmuebles24.com/inmuebles-en-fraccionamiento-terralta.html",
    "https://www.inmuebles24.com/casas-en-fraccionamiento-terralta-jalisco.html",
    "https://www.inmuebles24.com/propiedades-en-venta-en-fraccionamiento-terralta-jalisco.html",
    "https://www.inmuebles24.com/inmuebles-en-san-pedro-tlaquepaque-jalisco.html?q=terralta",
]

ML_URLS = {
    "Venta": "https://inmuebles.mercadolibre.com.mx/casas/venta/jalisco/san-pedro-tlaquepaque/terralta/",
    "Renta": "https://inmuebles.mercadolibre.com.mx/casas/alquiler/jalisco/san-pedro-tlaquepaque/terralta/",
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


# ── requests helper ───────────────────────────────────────────────────────────
def fetch_html(url: str) -> str | None:
    """Fetch HTML con requests (sin browser). Ideal para páginas SSR/Next.js."""
    session = req.Session()
    session.headers.update(HEADERS)
    try:
        # Warm-up cookie
        time.sleep(random.uniform(0.5, 1.5))
        session.get("https://www.inmuebles24.com/", timeout=20, allow_redirects=True)
        time.sleep(random.uniform(1, 2))
        resp = session.get(url, timeout=30, allow_redirects=True)
        if resp.status_code == 200:
            return resp.text
        print(f"    HTTP {resp.status_code} en {url}")
        return None
    except Exception as e:
        print(f"    requests error: {e}")
        return None


# ── __NEXT_DATA__ extractor ───────────────────────────────────────────────────
def extract_next_data(html: str) -> dict | None:
    """Extrae JSON de <script id='__NEXT_DATA__'>"""
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
    if not m:
        m = re.search(r'__NEXT_DATA__\s*=\s*(\{.*?\})\s*;', html, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception as e:
            print(f"    ⚠ JSON parse error: {e}")
    return None


def find_postings(obj, depth=0) -> list | None:
    """Búsqueda recursiva de array de postings en __NEXT_DATA__"""
    if depth > 8:
        return None
    if isinstance(obj, list) and len(obj) >= 1 and isinstance(obj[0], dict):
        # Señales de que es un array de listings inmobiliarios
        sample = obj[0]
        if any(k in sample for k in ("postingUrl", "operationType", "priceOperationTypes", "realEstateType")):
            return obj
    if isinstance(obj, dict):
        # Claves prioritarias primero
        priority = ["postings", "listings", "results", "items", "data"]
        for k in priority:
            if k in obj:
                result = find_postings(obj[k], depth + 1)
                if result:
                    return result
        for k, v in obj.items():
            if k in priority:
                continue
            result = find_postings(v, depth + 1)
            if result:
                return result
    return None


def parse_i24_posting(p: dict) -> dict | None:
    """Parsea un posting de Inmuebles24 desde __NEXT_DATA__"""
    # Precio
    precio = None
    try:
        for op in p.get("priceOperationTypes", []):
            for price_obj in op.get("prices", []):
                amt = price_obj.get("amount", 0)
                cur = price_obj.get("currency", "MXN")
                if amt:
                    precio = int(float(amt))
                    if cur == "USD":
                        precio = int(precio * 17.5)
                    break
    except Exception:
        pass

    if not precio:
        return None

    # Tipo de operación
    op_raw = str(p.get("operationType", "")).lower()
    modalidad = "Renta" if "alquiler" in op_raw or "renta" in op_raw else "Venta"

    # Tipo de propiedad
    tipo_obj = p.get("realEstateType", {})
    tipo_name = tipo_obj.get("name", "Casa") if isinstance(tipo_obj, dict) else str(tipo_obj)
    tipo_prop = "Depto" if "departamento" in tipo_name.lower() or "depto" in tipo_name.lower() else "Casa"

    # Features (m², rec, ban, estac)
    m2c = rec = ban = estac = None
    features = p.get("mainFeatures", {})
    if isinstance(features, dict):
        for _key, feat in features.items():
            if not isinstance(feat, dict):
                continue
            name = feat.get("name", "").lower()
            val = feat.get("value")
            if val is None:
                continue
            try:
                if any(x in name for x in ("covered", "cubierta", "construida")):
                    m2c = int(float(val))
                elif any(x in name for x in ("bedroom", "dormitorio", "recamara", "recámara", "habitacion")):
                    rec = int(float(val))
                elif any(x in name for x in ("bathroom", "baño", "bano")):
                    ban = int(float(val))
                elif any(x in name for x in ("parking", "estacionamiento", "cochera")):
                    estac = int(float(val))
            except (ValueError, TypeError):
                pass
    # También intentar con surface directamente
    if not m2c:
        for key in ("coveredArea", "totalArea", "surface"):
            if key in p:
                try:
                    m2c = int(float(p[key]))
                    break
                except Exception:
                    pass

    # Imagen
    img = None
    try:
        media = p.get("media", {})
        photos = media.get("photos", []) if isinstance(media, dict) else []
        if photos:
            raw = photos[0].get("url", "") or photos[0].get("src", "")
            img = raw.replace("360x266", "720x532").split("?")[0] if raw else None
    except Exception:
        pass

    # ID y URL
    listing_id = str(p.get("id") or p.get("publicationId") or p.get("listingId") or "")
    url = p.get("postingUrl") or (
        f"https://www.inmuebles24.com/propiedades-{listing_id}.html" if listing_id else None
    )

    # Dirección
    geo = p.get("geo", {})
    address = ""
    if isinstance(geo, dict):
        address = geo.get("address") or geo.get("street") or geo.get("location") or ""
    if not address:
        address = str(p.get("title", "Terralta, Tlaquepaque"))[:60]

    remate = bool(re.search(r"remate|bancario", str(p).lower()))
    lat, lng = guess_coords(address)
    calle_short = address.split(",")[0].strip() if address else "Terralta"

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


# ── Scraper Inmuebles24 ───────────────────────────────────────────────────────
async def scrape_inmuebles24(page) -> list[dict]:
    print("  → Inmuebles24 (requests + __NEXT_DATA__)...")

    # 1) Intentar con requests (más rápido, sin detección de bot headless)
    for url in I24_URLS:
        html = fetch_html(url)
        if not html:
            continue

        nd = extract_next_data(html)
        if nd:
            postings = find_postings(nd)
            if postings:
                listings = [parse_i24_posting(p) for p in postings]
                listings = [l for l in listings if l]
                if listings:
                    print(f"  ✓ {len(listings)} propiedades Inmuebles24 (requests+JSON)")
                    return listings

        # Si no hay __NEXT_DATA__ aún puede haber cards en HTML raw
        cards_raw = re.findall(r'"id"\s*:\s*"(\d{8,12})"', html)
        if cards_raw:
            print(f"    Encontrados {len(cards_raw)} IDs raw en HTML, usando DOM...")
            break

    # 2) Fallback: Playwright con stealth
    print("  → Inmuebles24 fallback: Playwright + stealth...")
    if HAS_STEALTH:
        await stealth_async(page)

    for url in I24_URLS[:2]:
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            # Esperar renderizado JS
            await asyncio.sleep(5)

            # Intentar __NEXT_DATA__ desde browser
            nd_str = await page.evaluate(
                "() => { const s = document.getElementById('__NEXT_DATA__'); return s ? s.textContent : null; }"
            )
            if nd_str:
                try:
                    nd = json.loads(nd_str)
                    postings = find_postings(nd)
                    if postings:
                        listings = [parse_i24_posting(p) for p in postings]
                        listings = [l for l in listings if l]
                        if listings:
                            print(f"  ✓ {len(listings)} propiedades Inmuebles24 (Playwright+JSON)")
                            return listings
                except Exception as e:
                    print(f"    ⚠ Playwright JSON parse: {e}")

            # DOM fallback — intentar múltiples selectores
            selectors = [
                "[data-id]",
                "[data-postingid]",
                ".posting-card-desktop",
                ".listing-card",
                "article.posting",
                "[class*='posting-card']",
                "[class*='ListingCard']",
            ]
            cards = []
            for sel in selectors:
                try:
                    await page.wait_for_selector(sel, timeout=8000)
                    cards = await page.query_selector_all(sel)
                    if cards:
                        print(f"    Selector '{sel}' encontró {len(cards)} cards")
                        break
                except Exception:
                    pass

            if cards:
                return await parse_dom_cards(cards)

        except Exception as e:
            print(f"    ⚠ Playwright error ({url}): {e}")
            continue

    print("  ✗ Inmuebles24: 0 propiedades obtenidas")
    return []


async def parse_dom_cards(cards) -> list[dict]:
    """Parsea cards DOM como último recurso"""
    listings = []
    for card in cards:
        try:
            listing_id = (
                await card.get_attribute("data-id")
                or await card.get_attribute("data-postingid")
                or ""
            )
            if not listing_id or len(listing_id) < 6:
                continue

            text = await card.inner_text()
            lines = [l.strip() for l in text.split("\n") if l.strip()]

            # Precio
            precio_raw = next((l for l in lines if re.match(r"^MN\s[\d,]+$|^\$\s*[\d,]+$", l)), None)
            precio = int(re.sub(r"[^\d]", "", precio_raw)) if precio_raw else None
            if not precio:
                continue

            img_el = await card.query_selector("img")
            img_src = ""
            if img_el:
                img_src = (await img_el.get_attribute("data-src") or await img_el.get_attribute("src") or "")
                img_src = img_src.replace("360x266", "720x532").split("?")[0]

            m2_m = re.search(r"(\d+)\s*m²", text)
            rec_m = re.search(r"(\d+)\s*rec", text, re.I)
            ban_m = re.search(r"(\d+)\s*ba[ñn]", text, re.I)
            est_m = re.search(r"(\d+)\s*estac", text, re.I)

            modalidad = "Renta" if precio < 50000 else "Venta"
            tipo_prop = "Depto" if "departamento" in text.lower() else "Casa"
            remate = bool(re.search(r"remate|bancario", text.lower()))

            skip = r"^(MN\s|\$[\d,]|\d+\s*m²|\d+\s*(rec|ba[ñn]|estac)|\+\d+\s*foto|^\d+$)"
            calle_cands = [l for l in lines if not re.search(skip, l, re.I) and len(l) > 6 and not l[0].isdigit()]
            calle = calle_cands[0] if calle_cands else "Terralta, San Pedro Tlaquepaque"
            lat, lng = guess_coords(calle)

            listings.append({
                "id": listing_id,
                "modalidad": modalidad,
                "tipoProp": tipo_prop,
                "titulo": f"{'Casa' if tipo_prop == 'Casa' else 'Depto'} {'Remate' if remate else 'en ' + modalidad} — {calle.split(',')[0]}",
                "calle": calle,
                "precio": precio,
                "m2c": int(m2_m.group(1)) if m2_m else None,
                "rec": int(rec_m.group(1)) if rec_m else None,
                "ban": int(ban_m.group(1)) if ban_m else None,
                "estac": int(est_m.group(1)) if est_m else None,
                "extras": "",
                "remate": remate,
                "lat": lat,
                "lng": lng,
                "fuente": "Inmuebles24",
                "img": img_src or None,
                "url": f"https://www.inmuebles24.com/propiedades-{listing_id}.html",
                "listing_id": listing_id,
            })
        except Exception as e:
            print(f"    ⚠ DOM card error: {e}")
    return listings


# ── Scraper MercadoLibre ──────────────────────────────────────────────────────
async def scrape_mercadolibre(page, url: str, modalidad: str) -> list[dict]:
    print(f"  → MercadoLibre ({modalidad})...")
    if HAS_STEALTH:
        await stealth_async(page)
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(3)

        # Intentar __NEXT_DATA__ / __PRELOADED_STATE__
        for var in ["__NEXT_DATA__", "__PRELOADED_STATE__"]:
            nd_str = await page.evaluate(
                f"() => {{ const s = document.getElementById('{var}'); return s ? s.textContent : (window.{var} ? JSON.stringify(window.{var}) : null); }}"
            )
            if nd_str:
                try:
                    data = json.loads(nd_str)
                    postings = find_postings(data)
                    if postings and len(postings) > 0:
                        listings = []
                        for p in postings:
                            try:
                                l = parse_ml_item(p, modalidad)
                                if l:
                                    listings.append(l)
                            except Exception:
                                pass
                        if listings:
                            print(f"  ✓ {len(listings)} propiedades ML ({modalidad}) via JSON")
                            return listings
                except Exception:
                    pass

        # DOM fallback
        selectors = [
            ".ui-search-result",
            "[class*='ui-search-result']",
            ".poly-card",
            "li.ui-search-layout__item",
        ]
        cards = []
        for sel in selectors:
            try:
                await page.wait_for_selector(sel, timeout=10000)
                cards = await page.query_selector_all(sel)
                if cards:
                    break
            except Exception:
                pass

        if not cards:
            print(f"  ✗ MercadoLibre ({modalidad}): no cards encontradas")
            return []

        listings = []
        for card in cards:
            try:
                text = await card.inner_text()

                # Precio
                precio_raw = next((l.strip() for l in text.split("\n") if re.match(r"^\$[\s\d,.]+$", l.strip())), None)
                if not precio_raw:
                    continue
                precio = int(re.sub(r"[^\d]", "", precio_raw))

                # Filtro de rango
                if modalidad == "Venta" and not (200_000 <= precio <= 15_000_000):
                    continue
                if modalidad == "Renta" and not (3_000 <= precio <= 80_000):
                    continue

                img_el = await card.query_selector("img")
                img_src = ""
                if img_el:
                    img_src = (
                        await img_el.get_attribute("data-src")
                        or await img_el.get_attribute("src")
                        or ""
                    )

                title_el = await card.query_selector("h2, .ui-search-item__title, [class*='title']")
                titulo = (await title_el.inner_text()).strip() if title_el else "Propiedad en Terralta"

                m2_m = re.search(r"(\d+)\s*m²", text)
                rec_m = re.search(r"(\d+)\s*rec[aá]m", text, re.I)
                ban_m = re.search(r"(\d+)\s*ba[ñn]", text, re.I)

                tipo_prop = "Depto" if "departamento" in titulo.lower() else "Casa"
                remate = bool(re.search(r"remate|bancario", titulo.lower()))

                link_el = await card.query_selector("a[href]")
                href = (await link_el.get_attribute("href") or "") if link_el else ""
                id_m = re.search(r"MLM-?(\d+)", href)
                listing_id = f"MLM-{id_m.group(1)}" if id_m else f"ml-{len(listings)}"

                calle = "Terralta, Tlaquepaque"
                lat, lng = guess_coords(calle)

                listings.append({
                    "id": listing_id,
                    "modalidad": modalidad,
                    "tipoProp": tipo_prop,
                    "titulo": titulo[:80],
                    "calle": calle,
                    "precio": precio,
                    "m2c": int(m2_m.group(1)) if m2_m else None,
                    "rec": int(rec_m.group(1)) if rec_m else None,
                    "ban": int(ban_m.group(1)) if ban_m else None,
                    "estac": None,
                    "extras": "",
                    "remate": remate,
                    "lat": lat,
                    "lng": lng,
                    "fuente": "MercadoLibre",
                    "img": img_src or None,
                    "url": href or None,
                    "listing_id": listing_id,
                })
            except Exception as e:
                print(f"    ⚠ ML card error: {e}")

        print(f"  ✓ {len(listings)} propiedades ML ({modalidad}) via DOM")
        return listings

    except Exception as e:
        print(f"  ✗ MercadoLibre ({modalidad}) error: {e}")
        return []


def parse_ml_item(item: dict, modalidad: str) -> dict | None:
    """Parsea item de MercadoLibre desde JSON"""
    precio = None
    try:
        precio = int(float(item.get("price") or item.get("sale_price") or 0))
    except Exception:
        pass
    if not precio:
        return None

    img = item.get("thumbnail") or item.get("pictures", [{}])[0].get("url", "") if item.get("pictures") else ""
    titulo = item.get("title", "Propiedad en Terralta")[:80]
    listing_id = str(item.get("id", ""))
    url = item.get("permalink") or item.get("url") or ""
    tipo_prop = "Depto" if "departamento" in titulo.lower() else "Casa"
    remate = bool(re.search(r"remate|bancario", titulo.lower()))
    calle = "Terralta, Tlaquepaque"
    lat, lng = guess_coords(calle)

    attrs = {a.get("id", ""): a.get("value_name", "") for a in item.get("attributes", []) if isinstance(a, dict)}
    m2c = None
    rec = None
    ban = None
    try:
        m2c = int(float(attrs.get("COVERED_AREA", 0) or 0)) or None
        rec = int(float(attrs.get("BEDROOMS", 0) or 0)) or None
        ban = int(float(attrs.get("BATHROOMS", 0) or 0)) or None
    except Exception:
        pass

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
        "estac": None,
        "extras": "",
        "remate": remate,
        "lat": lat,
        "lng": lng,
        "fuente": "MercadoLibre",
        "img": img or None,
        "url": url or None,
        "listing_id": listing_id,
    }


# ── Utilidades ────────────────────────────────────────────────────────────────
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
            existing = json.loads(OUTPUT_FILE.read_text("utf-8"))
            return existing.get("desarrollos", [])
        except Exception:
            pass
    return []


# ── Main ──────────────────────────────────────────────────────────────────────
async def main():
    print("\n🏠 Cloud Inmobiliaria · Scraper v2.1")
    print(f"   Zona: Fraccionamiento Terralta, San Pedro Tlaquepaque")
    print(f"   Hora: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"   Stealth: {'✓ disponible' if HAS_STEALTH else '✗ no instalado (pip install playwright-stealth)'}\n")

    all_listings: list[dict] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-web-security",
                "--disable-features=IsolateOrigins,site-per-process",
            ],
        )
        context = await browser.new_context(
            user_agent=HEADERS["User-Agent"],
            viewport={"width": 1280, "height": 900},
            locale="es-MX",
            timezone_id="America/Mexico_City",
            extra_http_headers={
                "Accept-Language": "es-MX,es;q=0.9",
            },
        )
        await context.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
            "window.chrome={runtime:{}};"
            "Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3,4,5]});"
        )

        # Bloquear recursos pesados innecesarios
        await context.route(
            "**/*.{woff,woff2,ttf,otf,mp4,webm,avi}",
            lambda route: route.abort(),
        )

        page = await context.new_page()

        # ── Inmuebles24 ──
        try:
            i24 = await scrape_inmuebles24(page)
            all_listings.extend(i24)
        except Exception as e:
            print(f"  ✗ Inmuebles24 fatal: {e}")

        # ── MercadoLibre Venta ──
        try:
            ml_v = await scrape_mercadolibre(page, ML_URLS["Venta"], "Venta")
            all_listings.extend(ml_v)
        except Exception as e:
            print(f"  ✗ ML Venta fatal: {e}")

        # ── MercadoLibre Renta ──
        try:
            ml_r = await scrape_mercadolibre(page, ML_URLS["Renta"], "Renta")
            all_listings.extend(ml_r)
        except Exception as e:
            print(f"  ✗ ML Renta fatal: {e}")

        await browser.close()

    # ── Limpiar ──
    all_listings = [p for p in all_listings if p.get("precio") and p["precio"] > 0]
    all_listings = dedup(all_listings)

    # ── Guard: nunca sobrescribir con datos vacíos ──
    if len(all_listings) == 0:
        print("\n⚠️  Scraping resultó en 0 propiedades.")
        print("   El archivo de datos NO fue modificado.")
        print("   Revisa conectividad o cambios en la estructura de los portales.\n")
        sys.exit(0)  # exit 0 para que el workflow no falle (es recuperable)

    all_listings = assign_ids(all_listings)
    # Ordenar: ventas primero, luego rentas; dentro de cada grupo por precio
    all_listings.sort(key=lambda p: (p.get("modalidad") == "Renta", p.get("precio", 0)))

    desarrollos = load_desarrollos()
    fuentes_usadas = sorted({p["fuente"] for p in all_listings})

    cdt = timezone(timedelta(hours=-5))
    ts = datetime.now(cdt).isoformat(timespec="seconds")

    output = {
        "_meta": {
            "actualizado": ts,
            "fuentes": fuentes_usadas,
            "zona": "Fraccionamiento Terralta, San Pedro Tlaquepaque",
            "total": len(all_listings),
            "scraper_version": "2.1",
        },
        "propiedades": all_listings,
        "desarrollos": desarrollos,
    }

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(output, ensure_ascii=False, indent=2), "utf-8")

    print(f"\n✅ {len(all_listings)} propiedades guardadas → {OUTPUT_FILE}")
    print(f"   Fuentes: {', '.join(fuentes_usadas)}")
    print(f"   Actualizado: {ts}\n")


if __name__ == "__main__":
    asyncio.run(main())
