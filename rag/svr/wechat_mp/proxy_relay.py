"""
Proxy Relay for WeChat MP — equivalent to we-mp-rss tools/deno_proxy.ts.

Deploy this as a standalone service on a cloud platform (Cloudflare Workers,
Deno Deploy, Railway, a cheap VPS, etc.) to route WeChat requests through a
different IP address, bypassing IP-level anti-bot blocking.

Usage as a standalone server:
    python rag/svr/wechat_mp/proxy_relay.py --port 8899

Usage as a WSGI/ASGI app (for cloud deployment):
    from rag.svr.wechat_mp.proxy_relay import app
    # app is a Quart (async Flask) application

Environment variables:
    PROXY_RELAY_PORT — listen port (default: 8899)
    PROXY_RELAY_HOST — listen host (default: 0.0.0.0)
    PROXY_RELAY_TOKEN — optional auth token (checks ?token= or Authorization header)
"""

import logging
import os

from quart import Quart, request, Response
import httpx

logger = logging.getLogger(__name__)

# ── Allowed domains (same as we-mp-rss) ──────────────────────
ALLOWED_DOMAINS = {
    "mp.weixin.qq.com",
    "weixin.qq.com",
    "mmbiz.qpic.cn",
    "mmbiz.qlogo.cn",
}

# ── Headers to forward from client to target ─────────────────
FORWARD_HEADERS = [
    "user-agent",
    "cookie",
    "authorization",
    "accept",
    "accept-language",
    "accept-encoding",
]

# ── Default User-Agent ──────────────────────────────────────
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

app = Quart(__name__)
_AUTH_TOKEN = os.environ.get("PROXY_RELAY_TOKEN", "")


def _check_auth() -> bool:
    """Check if request is authorized (token in query or header)."""
    if not _AUTH_TOKEN:
        return True
    if request.args.get("token") == _AUTH_TOKEN:
        return True
    auth_header = request.headers.get("Authorization", "")
    if auth_header == f"Bearer {_AUTH_TOKEN}":
        return True
    return False


def _is_allowed(url: str) -> bool:
    """Check if the target URL's domain is in the allow-list."""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    return hostname in ALLOWED_DOMAINS


@app.route("/proxy", methods=["GET", "POST"])
async def proxy_handler():
    """Relay an HTTP request to a WeChat domain.

    GET  /proxy?url=https://mp.weixin.qq.com/s/...
    POST /proxy  with JSON {"url": "https://mp.weixin.qq.com/s/..."}
    """
    # Auth check
    if not _check_auth():
        return {"error": "Unauthorized"}, 401

    # Extract target URL
    if request.method == "POST":
        try:
            body = await request.get_json()
            target_url = (body or {}).get("url", "")
        except Exception:
            target_url = ""
    else:
        target_url = request.args.get("url", "")

    if not target_url:
        return {"error": "Missing url parameter"}, 400

    # Security: domain allow-list
    if not _is_allowed(target_url):
        return {"error": f"Domain not allowed: {target_url}"}, 403

    # Security: only HTTP(S)
    if not target_url.startswith(("http://", "https://")):
        return {"error": "Only HTTP(S) URLs are allowed"}, 400

    # Build forwarded headers
    headers = {}
    for h in FORWARD_HEADERS:
        val = request.headers.get(h)
        if val:
            headers[h] = val
    if "user-agent" not in headers:
        headers["user-agent"] = DEFAULT_UA

    # Forward the request
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            resp = await client.get(target_url, headers=headers)

        content_type = resp.headers.get("content-type", "text/html")

        # Determine if binary (image) or text
        is_image = any(t in content_type for t in ("image/", "application/octet-stream"))

        if is_image:
            return Response(
                resp.content,
                status=resp.status_code,
                headers={
                    "Content-Type": content_type,
                    "Access-Control-Allow-Origin": "*",
                    "Cache-Control": "public, max-age=3600",
                },
            )
        else:
            return Response(
                resp.text,
                status=resp.status_code,
                headers={
                    "Content-Type": content_type,
                    "Access-Control-Allow-Origin": "*",
                },
            )
    except httpx.TimeoutException:
        return {"error": "Upstream timeout"}, 504
    except Exception as e:
        logger.error("Proxy error: %s", e)
        return {"error": str(e)}, 502


@app.route("/health", methods=["GET"])
async def health():
    return {"status": "ok"}


# ── CLI entry point for standalone deployment ────────────────
def main():
    """Run the proxy relay as a standalone server."""
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))
    ))))

    port = int(os.environ.get("PROXY_RELAY_PORT", "8899"))
    host = os.environ.get("PROXY_RELAY_HOST", "0.0.0.0")

    logging.basicConfig(level=logging.INFO)
    logger.info("Starting WeChat Proxy Relay on %s:%d", host, port)
    logger.info("Allowed domains: %s", ", ".join(sorted(ALLOWED_DOMAINS)))

    from hypercorn.asyncio import serve
    from hypercorn.config import Config

    config = Config()
    config.bind = [f"{host}:{port}"]
    import asyncio
    asyncio.run(serve(app, config))


if __name__ == "__main__":
    main()
