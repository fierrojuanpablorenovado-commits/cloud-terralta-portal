// Vercel Edge Function — proxy de imágenes naventcdn
// Agrega Referer válido de Inmuebles24 para bypass hotlink protection
export const config = { runtime: 'edge' };

const ALLOWED_HOSTS = ['img10.naventcdn.com', 'lasnubesresidencial.com', 'altosur.com.mx', 'http2.mlstatic.com'];

export default async function handler(req) {
  const { searchParams } = new URL(req.url);
  const imgUrl = searchParams.get('url');

  if (!imgUrl) {
    return new Response('Missing url param', { status: 400 });
  }

  let parsed;
  try { parsed = new URL(imgUrl); } catch {
    return new Response('Invalid URL', { status: 400 });
  }

  // Solo permitir hosts conocidos
  const hostOk = ALLOWED_HOSTS.some(h => parsed.hostname === h || parsed.hostname.endsWith('.' + h));
  if (!hostOk) {
    return new Response('Host not allowed', { status: 403 });
  }

  const upstream = await fetch(imgUrl, {
    headers: {
      'Referer': 'https://www.inmuebles24.com/',
      'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
      'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8',
    },
  });

  if (!upstream.ok) {
    return new Response('Upstream error ' + upstream.status, { status: upstream.status });
  }

  const contentType = upstream.headers.get('content-type') || 'image/jpeg';
  const body = await upstream.arrayBuffer();

  return new Response(body, {
    status: 200,
    headers: {
      'Content-Type': contentType,
      'Cache-Control': 'public, max-age=86400, stale-while-revalidate=604800',
      'Access-Control-Allow-Origin': '*',
    },
  });
}
