#!/usr/bin/env python3
"""
localize_images.py — Descarga TODAS las imágenes externas (naventcdn, mlstatic, etc.)
y las guarda en data/img/ como archivos estáticos. Luego reescribe mapa.html y
listings.json para que apunten a las copias locales.

¿Por qué? Las imágenes hotlinkeadas de Inmuebles24/MercadoLibre EXPIRAN y cada
red/usuario puede verlas distinto. Al hospedarlas en el propio repo (Vercel CDN),
TODOS ven exactamente las mismas imágenes, siempre, sin depender de CDNs externos.

Idempotente: usa hash MD5 del URL como nombre de archivo. Re-ejecutar solo baja
lo que falta. Seguro para correr en GitHub Actions.
"""
import os, re, json, hashlib, sys

try:
    from curl_cffi import requests as creq
    HAS_CURL_CFFI = True
except ImportError:
    import requests as creq
    HAS_CURL_CFFI = False

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMG_DIR = os.path.join(ROOT, "data", "img")
MAPA = os.path.join(ROOT, "mapa.html")
LISTINGS = os.path.join(ROOT, "data", "listings.json")

HEADERS = {
    "Referer": "https://www.inmuebles24.com/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
}

# Hosts cuyas imágenes localizamos (los que expiran o bloquean hotlink)
EXTERNAL_HOSTS = ("naventcdn.com", "mlstatic.com", "lasnubesresidencial.com",
                  "altosur.com.mx", "scontent", "fbcdn.net")


def is_external(url):
    if not url or not url.startswith("http"):
        return False
    return any(h in url for h in EXTERNAL_HOSTS)


def local_name(url):
    """Nombre de archivo determinista basado en hash del URL."""
    h = hashlib.md5(url.encode("utf-8")).hexdigest()[:16]
    ext = ".jpg"
    if ".png" in url.lower():
        ext = ".png"
    elif ".webp" in url.lower():
        ext = ".webp"
    return f"{h}{ext}"


def download(url, dest):
    try:
        if HAS_CURL_CFFI:
            r = creq.get(url, headers=HEADERS, impersonate="chrome", timeout=20)
        else:
            r = creq.get(url, headers=HEADERS, timeout=20)
        if r.status_code == 200 and len(r.content) > 1000:
            with open(dest, "wb") as f:
                f.write(r.content)
            return True
        print(f"  ✗ HTTP {r.status_code} ({len(r.content)}b) {url[:60]}")
    except Exception as e:
        print(f"  ✗ error {type(e).__name__}: {url[:60]}")
    return False


def collect_urls():
    """Junta todas las URLs externas de mapa.html y listings.json."""
    urls = set()

    # mapa.html — campos img: 'URL'
    if os.path.exists(MAPA):
        html = open(MAPA, encoding="utf-8").read()
        for m in re.findall(r"img:\s*[\"']([^\"']+)[\"']", html):
            if is_external(m):
                urls.add(m)

    # listings.json — campo img
    if os.path.exists(LISTINGS):
        data = json.load(open(LISTINGS, encoding="utf-8"))
        for p in data.get("propiedades", []):
            if is_external(p.get("img")):
                urls.add(p["img"])
        for d in data.get("desarrollos", []):
            if is_external(d.get("img")):
                urls.add(d["img"])

    return urls


def main():
    os.makedirs(IMG_DIR, exist_ok=True)
    urls = collect_urls()
    print(f"📸 {len(urls)} imágenes externas encontradas")

    mapping = {}   # url -> /data/img/<name>
    ok, skip, fail = 0, 0, 0

    for url in sorted(urls):
        name = local_name(url)
        dest = os.path.join(IMG_DIR, name)
        rel = f"/data/img/{name}"
        if os.path.exists(dest) and os.path.getsize(dest) > 1000:
            mapping[url] = rel
            skip += 1
            continue
        if download(url, dest):
            mapping[url] = rel
            ok += 1
            print(f"  ✓ {rel}")
        else:
            fail += 1

    print(f"\n✅ Descargadas: {ok} · ♻️ Ya existían: {skip} · ✗ Fallaron: {fail}")

    # Reescribir mapa.html
    if os.path.exists(MAPA) and mapping:
        html = open(MAPA, encoding="utf-8").read()
        n = 0
        for url, rel in mapping.items():
            if url in html:
                html = html.replace(url, rel)
                n += 1
        open(MAPA, "w", encoding="utf-8").write(html)
        print(f"📝 mapa.html: {n} URLs reemplazadas por copias locales")

    # Reescribir listings.json
    if os.path.exists(LISTINGS) and mapping:
        data = json.load(open(LISTINGS, encoding="utf-8"))
        n = 0
        for p in data.get("propiedades", []):
            if p.get("img") in mapping:
                p["img"] = mapping[p["img"]]
                n += 1
        for d in data.get("desarrollos", []):
            if d.get("img") in mapping:
                d["img"] = mapping[d["img"]]
                n += 1
        json.dump(data, open(LISTINGS, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        print(f"📝 listings.json: {n} URLs reemplazadas por copias locales")


if __name__ == "__main__":
    main()
