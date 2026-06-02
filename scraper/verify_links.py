#!/usr/bin/env python3
"""
verify_links.py — Verifica diariamente que los links directos del portal sigan vivos.
Cuando una propiedad muere (404 real en browser), la marca como inactiva en mapa.html.

Cómo funciona:
- Lee mapa.html y extrae los objetos PROPS con id + url
- Para cada URL "directa" (no búsqueda genérica), verifica con curl_cffi
- Si da 404 o redirige a handleUrlNotRecognize → marca la prop como removida
- Guarda un reporte de cambios

Criterios para considerar "muerta" una propiedad:
- HTTP 404
- HTTP 410 (Gone)
- Redirige a página de búsqueda genérica o handleUrlNotRecognize
- HTTP 200 pero con redirect a home/listado

Criterios para NO eliminar:
- HTTP 403 (bloqueo anti-bot — el anuncio existe pero bloquea scripts)
- HTTP 429 (rate limit — anuncio existe)
- Cloudflare challenge (anuncio existe)
- Error de conexión (problema de red, no del anuncio)
"""

import os, re, json, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAPA = os.path.join(ROOT, "mapa.html")

try:
    from curl_cffi import requests as creq
    HAS_CURL = True
except ImportError:
    import requests as creq
    HAS_CURL = False

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-MX,es;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "no-cache",
}

# URLs/patrones que indican que el anuncio ya no existe
DEAD_PATTERNS = [
    "handleUrlNotRecognize",
    "foreclosures/for-sale",
    "/404",
    "?error=404",
    "pagina-no-encontrada",
]

# URLs genéricas (búsquedas, no anuncios específicos) — no verificar
GENERIC_PATTERNS = [
    "listado.mercadolibre",
    "jalisco/tlaquepaque/terralta",
    "query=renta", "query=casa", "query=remate", "query=departamento",
    "marketplace/guadalajara/search",
]

# Códigos que NO significan "muerto"
SAFE_CODES = {403, 429, 503, 521, 522, 523, 524, 530}


def is_generic(url: str) -> bool:
    return not url or any(p in url for p in GENERIC_PATTERNS)


def check_url(url: str) -> tuple[str, str]:
    """Retorna (status, reason) donde status es 'alive'|'dead'|'unknown'."""
    if is_generic(url):
        return "skip", "URL genérica"
    try:
        if HAS_CURL:
            r = creq.get(url, impersonate="chrome", headers=HEADERS,
                         timeout=15, allow_redirects=True)
        else:
            r = creq.get(url, headers=HEADERS, timeout=15, allow_redirects=True)

        code = r.status_code
        final = str(r.url)

        # 403/429/etc = bloqueado pero existe
        if code in SAFE_CODES:
            return "alive", f"HTTP {code} (bloqueo anti-bot, anuncio existe)"

        # 404/410 = muerto
        if code in (404, 410):
            return "dead", f"HTTP {code}"

        # Redirigió a página de error
        for dead_p in DEAD_PATTERNS:
            if dead_p in final:
                return "dead", f"Redirigió a {dead_p}"

        # Cloudflare challenge = existe
        if "challenge" in final.lower() or "cf-browser-verification" in final.lower():
            return "alive", "Cloudflare challenge (anuncio existe)"

        if code == 200:
            return "alive", f"HTTP 200"

        return "unknown", f"HTTP {code}"

    except Exception as e:
        return "unknown", f"Error: {str(e)[:40]}"


def extract_props_with_urls(html: str) -> list[dict]:
    """Extrae id + url de cada prop en el array PROPS de mapa.html."""
    # Encontrar bloque PROPS
    try:
        start = html.index("const PROPS = [")
    except ValueError:
        return []

    depth = 0
    i = start + len("const PROPS = ")
    end = i
    while i < len(html):
        if html[i] == "[":
            depth += 1
        elif html[i] == "]":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
        i += 1

    block = html[start:end]
    results = []
    for m in re.finditer(r"\{[^{}]*?\}", block, re.DOTALL):
        obj = m.group(0)
        id_m = re.search(r"\bid:\s*(\d+)", obj)
        url_m = re.search(r"\burl:\s*[\"']([^\"']+)[\"']", obj)
        titulo_m = re.search(r"\btitulo:\s*[\"']([^\"']+)[\"']", obj)
        fuente_m = re.search(r"\bfuente:\s*[\"']([^\"']+)[\"']", obj)
        if id_m:
            results.append({
                "id": int(id_m.group(1)),
                "url": url_m.group(1) if url_m else None,
                "titulo": titulo_m.group(1)[:40] if titulo_m else "?",
                "fuente": fuente_m.group(1) if fuente_m else "?",
            })
    return results


def remove_prop_from_html(html: str, prop_id: int) -> str:
    """Elimina el objeto con id: N del array PROPS."""
    pattern = re.compile(
        r",\s*\n\s*\{[^{}]*?id:\s*" + str(prop_id) + r",[^{}]*?\}",
        re.DOTALL
    )
    return pattern.sub("", html)


def main():
    if not os.path.exists(MAPA):
        print("mapa.html no encontrado")
        return

    html = open(MAPA, encoding="utf-8").read()
    props = extract_props_with_urls(html)
    to_check = [p for p in props if p["url"] and not is_generic(p["url"])]

    print(f"Verificando {len(to_check)} URLs directas de {len(props)} propiedades...")
    print()

    dead_ids = []
    for p in to_check:
        status, reason = check_url(p["url"])
        icon = "OK  " if status == "alive" else ("DEAD" if status == "dead" else "????")
        print(f"  id:{p['id']:>3}  {icon}  {reason[:45]}  |  {p['url'][:50]}")
        if status == "dead":
            dead_ids.append(p["id"])
        time.sleep(0.5)

    print()
    if dead_ids:
        print(f"Eliminando {len(dead_ids)} propiedades inactivas: ids {dead_ids}")
        for id_ in dead_ids:
            html = remove_prop_from_html(html, id_)
            print(f"  Eliminado id:{id_}")
        open(MAPA, "w", encoding="utf-8").write(html)
        print(f"mapa.html actualizado — {len(dead_ids)} props eliminadas")
    else:
        print("Todas las URLs verificables siguen activas.")


if __name__ == "__main__":
    main()
