"""
Cloud Inmobiliaria — Scraper Automático
Extrae listings de Inmuebles24 y MercadoLibre para la zona Terralta
Corre vía GitHub Actions cada 24 horas

Dependencias:
  pip install playwright requests python-dateutil
  playwright install chromium
"""

import asyncio
import json
import re
import sys
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── Playwright ────────────────────────────────────────────────────────────────
try:
    from playwright.async_api import async_playwright
except ImportError:
    print("ERROR: playwright no instalado. Ejecuta: pip install playwright && playwright install chromium")
    sys.exit(1)

# ── Configuración ─────────────────────────────────────────────────────────────
OUTPUT_FILE = Path(__file__).parent.parent / "data" / "listings.json"

FUENTES = {
    "inmuebles24": {
        "url": "https://www.inmuebles24.com/inmuebles-en-fraccionamiento-terralta.html",
        "nombre": "Inmuebles24",
    },
    "mercadolibre_venta": {
        "url": "https://inmuebles.mercadolibre.com.mx/casas-departamentos/venta/jalisco/san-pedro-tlaquepaque/terralta/",
        "nombre": "MercadoLibre",
    },
    "mercadolibre_renta": {
        "url": "https://inmuebles.mercadolibre.com.mx/casas-departamentos/alquiler/jalisco/san-pedro-tlaquepaque/terralta/",
        "nombre": "MercadoLibre",
    },
}

# CDN naventcdn: ID → path de imágenes
def navent_img(listing_id: str, timestamp: str) -> str | None:
    """Construye URL de imagen naventcdn a partir del listing ID y timestamp"""
    # Padding a 12 dígitos con ceros a la derecha
    lid = str(listing_id).zfill(12)
    # Dividir en pares de 2 dígitos
    parts = [lid[i:i+2] for i in range(0, 12, 2)]
    path = "/".join(parts)
    return f"https://img10.naventcdn.com/avisos/{path}/720x532/{timestamp}.jpg"


# ── Scraper Inmuebles24 ───────────────────────────────────────────────────────
async def scrape_inmuebles24(page) -> list[dict]:
    print("  → Scrapeando Inmuebles24...")
    await page.goto(
        "https://www.inmuebles24.com/inmuebles-en-fraccionamiento-terralta.html",
        wait_until="networkidle",
        timeout=30000,
    )
    # Esperar que carguen los listings
    await page.wait_for_selector("[data-id]", timeout=15000)

    listings = []
    cards = await page.query_selector_all("[data-id]")

    for card in cards:
        try:
            listing_id = await card.get_attribute("data-id")
            if not listing_id or len(listing_id) < 8:
                continue

            # Texto completo del card
            text = await card.inner_text()
            lines = [l.strip() for l in text.split("\n") if l.strip()]

            # Precio (línea que empieza con MN + números)
            precio_raw = next(
                (l for l in lines if re.match(r"^MN\s[\d,]+$", l)), None
            )
            precio = int(re.sub(r"[^\d]", "", precio_raw)) if precio_raw else None

            # Imagen
            img_el = await card.query_selector("img")
            img_src = ""
            if img_el:
                img_src = await img_el.get_attribute("src") or ""
                # Subir resolución
                img_src = img_src.replace("360x266", "720x532").split("?")[0]

            # Specs: m2, rec, ban, estac
            m2_match = next(
                (l for l in lines if re.search(r"\d+\s*m²", l)), None
            )
            m2c = int(re.search(r"(\d+)\s*m²", m2_match).group(1)) if m2_match else None

            rec_match = next(
                (l for l in lines if re.search(r"\d+\s*rec", l, re.I)), None
            )
            rec = int(re.search(r"(\d+)\s*rec", rec_match, re.I).group(1)) if rec_match else None

            ban_match = next(
                (l for l in lines if re.search(r"\d+\s*ba[ñn]", l, re.I)), None
            )
            ban = int(re.search(r"(\d+)\s*ba[ñn]", ban_match, re.I).group(1)) if ban_match else None

            estac_match = next(
                (l for l in lines if re.search(r"\d+\s*estac", l, re.I)), None
            )
            estac = int(re.search(r"(\d+)\s*estac", estac_match, re.I).group(1)) if estac_match else None

            # Dirección: primera línea de texto que no sea precio/spec
            skip_patterns = r"^(MN\s|m²|\d+\s*(rec|ba[ñn]|estac)|\+\d+\s*foto|$)"
            calle = next(
                (l for l in lines if not re.search(skip_patterns, l, re.I) and len(l) > 6),
                "Terralta",
            )

            # Determinar modalidad y tipo
            text_lower = text.lower()
            modalidad = "Renta" if precio and precio < 50000 else "Venta"
            tipo_prop = "Depto" if "departamento" in text_lower or "depto" in text_lower else "Casa"
            remate = "remate" in text_lower or "bancario" in text_lower

            # Coordenadas aproximadas por calle
            lat, lng = guess_coords(calle)

            listings.append(
                {
                    "id": listing_id,
                    "modalidad": modalidad,
                    "tipoProp": tipo_prop,
                    "titulo": f"{'Casa' if tipo_prop=='Casa' else 'Depto'} {'Remate' if remate else 'en '+modalidad} — {calle.split(',')[0]}",
                    "calle": calle,
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
                    "img": img_src or None,
                    "listing_id": listing_id,
                }
            )
        except Exception as e:
            print(f"    ⚠ Error en card: {e}")
            continue

    print(f"  ✓ {len(listings)} propiedades extraídas de Inmuebles24")
    return listings


# ── Scraper MercadoLibre ──────────────────────────────────────────────────────
async def scrape_mercadolibre(page, url: str, modalidad: str) -> list[dict]:
    print(f"  → Scrapeando MercadoLibre ({modalidad})...")
    try:
        await page.goto(url, wait_until="networkidle", timeout=30000)
        await page.wait_for_selector(".ui-search-result", timeout=10000)
    except Exception:
        print("    ⚠ No se pudo cargar MercadoLibre, saltando...")
        return []

    listings = []
    cards = await page.query_selector_all(".ui-search-result")

    for card in cards:
        try:
            text = await card.inner_text()
            lines = [l.strip() for l in text.split("\n") if l.strip()]

            # Precio (línea con $ y números)
            precio_raw = next(
                (l for l in lines if re.match(r"^\$[\d,. ]+$", l)), None
            )
            if not precio_raw:
                continue
            precio = int(re.sub(r"[^\d]", "", precio_raw))

            # Filtro básico: sólo propiedades en rango razonable
            if modalidad == "Venta" and not (300000 <= precio <= 8000000):
                continue
            if modalidad == "Renta" and not (5000 <= precio <= 60000):
                continue

            # Imagen
            img_el = await card.query_selector("img.ui-search-result-image__element")
            img_src = ""
            if img_el:
                img_src = (
                    await img_el.get_attribute("data-src")
                    or await img_el.get_attribute("src")
                    or ""
                )

            # Título
            title_el = await card.query_selector(".ui-search-item__title, h2")
            titulo = (await title_el.inner_text()).strip() if title_el else f"Propiedad en Terralta"

            # Specs desde texto
            m2_m = re.search(r"(\d+)\s*m²", text)
            rec_m = re.search(r"(\d+)\s*recámara", text, re.I)
            ban_m = re.search(r"(\d+)\s*baño", text, re.I)

            tipo_prop = "Depto" if "departamento" in titulo.lower() else "Casa"
            remate = "remate" in titulo.lower()

            calle = "Terralta, Tlaquepaque"
            lat, lng = guess_coords(calle)

            # ID desde URL del card
            link_el = await card.query_selector("a[href*='MLM']")
            listing_id = ""
            if link_el:
                href = await link_el.get_attribute("href") or ""
                id_match = re.search(r"MLM-(\d+)", href)
                listing_id = f"MLM-{id_match.group(1)}" if id_match else ""

            listings.append(
                {
                    "id": listing_id or f"ml-{len(listings)+1}",
                    "modalidad": modalidad,
                    "tipoProp": tipo_prop,
                    "titulo": titulo[:80],
                    "calle": calle,
                    "precio": precio,
                    "m2c": int(m2_m.group(1)) if m2_m else None,
                    "rec": int(rec_m.group(1)) if rec_m else None,
                    "ban": int(ban_m.group(1)) if ban_m else None,
                    "estac": None,
                    "extras": "MercadoLibre",
                    "remate": remate,
                    "lat": lat,
                    "lng": lng,
                    "fuente": "MercadoLibre",
                    "img": img_src or None,
                    "listing_id": listing_id,
                }
            )
        except Exception as e:
            print(f"    ⚠ Error card ML: {e}")
            continue

    print(f"  ✓ {len(listings)} propiedades extraídas de MercadoLibre ({modalidad})")
    return listings


# ── Coordenadas por nombre de calle ──────────────────────────────────────────
COORD_MAP = {
    "tierra tranquila": (20.6100, -103.3545),
    "tierra encantada": (20.6108, -103.3538),
    "tierra contenta": (20.6120, -103.3525),
    "tierra hermosa": (20.6115, -103.3542),
    "tierra de unicornios": (20.6128, -103.3572),
    "tierra de ilusiones": (20.6118, -103.3565),
    "tierra del honor": (20.6102, -103.3550),
    "tierra de honor": (20.6100, -103.3548),
    "tierra de esperanza": (20.6098, -103.3554),
    "tierra de danza": (20.6110, -103.3565),
    "tierra de gloria": (20.6103, -103.3548),
    "las nubes": (20.6105, -103.3540),
    "nubes residencial": (20.6105, -103.3540),
    "terralta central": (20.6095, -103.3556),
    "coto san gabriel": (20.6130, -103.3570),
    "altosur": (20.6075, -103.3492),
    "alto sur": (20.6075, -103.3492),
}

CENTER = (20.6100, -103.3555)


def guess_coords(calle: str) -> tuple[float, float]:
    calle_lower = calle.lower()
    for key, coords in COORD_MAP.items():
        if key in calle_lower:
            return coords
    return CENTER


# ── Deduplicar ────────────────────────────────────────────────────────────────
def dedup(listings: list[dict]) -> list[dict]:
    """Elimina duplicados por precio + coordenadas similares"""
    seen = set()
    unique = []
    for p in listings:
        key = (p.get("precio"), round(p.get("lat", 0), 3), round(p.get("lng", 0), 3))
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique


# ── Asignar IDs secuenciales ──────────────────────────────────────────────────
def assign_ids(listings: list[dict]) -> list[dict]:
    for i, p in enumerate(listings, start=1):
        p["id"] = i
    return listings


# ── Cargar desarrollos fijos ──────────────────────────────────────────────────
def load_desarrollos() -> list[dict]:
    """Los desarrollos son datos semi-estáticos, se conservan entre runs"""
    if OUTPUT_FILE.exists():
        try:
            existing = json.loads(OUTPUT_FILE.read_text("utf-8"))
            return existing.get("desarrollos", [])
        except Exception:
            pass
    return []


# ── Main ──────────────────────────────────────────────────────────────────────
async def main():
    print("\n🏠 Cloud Inmobiliaria · Scraper Iniciado")
    print(f"   Zona: Fraccionamiento Terralta, San Pedro Tlaquepaque")
    print(f"   Hora: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    all_listings = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"],
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 900},
        )
        page = await context.new_page()

        # Bloquear recursos innecesarios para velocidad
        await page.route(
            "**/*.{png,jpg,jpeg,gif,svg,woff,woff2,ttf,mp4,webm}",
            lambda route: route.abort(),
        )

        # 1. Inmuebles24
        try:
            i24 = await scrape_inmuebles24(page)
            all_listings.extend(i24)
        except Exception as e:
            print(f"  ✗ Error Inmuebles24: {e}")

        # 2. MercadoLibre Venta
        try:
            ml_venta = await scrape_mercadolibre(page, FUENTES["mercadolibre_venta"]["url"], "Venta")
            all_listings.extend(ml_venta)
        except Exception as e:
            print(f"  ✗ Error MercadoLibre Venta: {e}")

        # 3. MercadoLibre Renta
        try:
            ml_renta = await scrape_mercadolibre(page, FUENTES["mercadolibre_renta"]["url"], "Renta")
            all_listings.extend(ml_renta)
        except Exception as e:
            print(f"  ✗ Error MercadoLibre Renta: {e}")

        await browser.close()

    # Limpiar y deduplicar
    all_listings = [p for p in all_listings if p.get("precio")]
    all_listings = dedup(all_listings)
    all_listings = assign_ids(all_listings)

    # Ordenar: primero venta, luego renta; dentro de cada grupo por precio
    all_listings.sort(key=lambda p: (p.get("modalidad") == "Renta", p.get("precio", 0)))

    desarrollos = load_desarrollos()
    fuentes_usadas = list({p["fuente"] for p in all_listings})

    # Timestamp en zona horaria CDT (UTC-5)
    cdt = timezone(timedelta(hours=-5))
    ts = datetime.now(cdt).isoformat(timespec="seconds")

    output = {
        "_meta": {
            "actualizado": ts,
            "fuentes": fuentes_usadas,
            "zona": "Fraccionamiento Terralta, San Pedro Tlaquepaque",
            "total": len(all_listings),
            "scraper_version": "1.0",
        },
        "propiedades": all_listings,
        "desarrollos": desarrollos,
    }

    # Escribir JSON
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(output, ensure_ascii=False, indent=2), "utf-8")

    print(f"\n✅ {len(all_listings)} propiedades guardadas → {OUTPUT_FILE}")
    print(f"   Fuentes: {', '.join(fuentes_usadas)}")
    print(f"   Actualizado: {ts}\n")


if __name__ == "__main__":
    asyncio.run(main())
