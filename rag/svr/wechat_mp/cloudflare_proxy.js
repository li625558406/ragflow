/**
 * Cloudflare Worker — WeChat MP Proxy Relay
 *
 * 部署步骤:
 * 1. 打开 https://dash.cloudflare.com/ → Workers & Pages → Create Worker
 * 2. 将此文件内容粘贴到编辑器
 * 3. 点击 "Deploy" 部署
 * 4. 获得 URL: https://your-worker-name.your-subdomain.workers.dev
 * 5. 将 URL 配到环境变量: WECHAT_PROXY_RELAY_URL=https://xxx.workers.dev/proxy
 *
 * 注意: Cloudflare Workers 免费版 10 万次/天，足够使用
 */

const ALLOWED_DOMAINS = [
  "mp.weixin.qq.com",
  "weixin.qq.com",
  "mmbiz.qpic.cn",
  "mmbiz.qlogo.cn",
];

function isAllowedDomain(url) {
  try {
    const parsed = new URL(url);
    return ALLOWED_DOMAINS.some(
      (domain) => parsed.hostname === domain || parsed.hostname.endsWith("." + domain)
    );
  } catch {
    return false;
  }
}

function corsHeaders() {
  return {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization, Cookie, User-Agent",
    "Access-Control-Max-Age": "86400",
  };
}

async function handleRequest(request) {
  const url = new URL(request.url);

  // Health check
  if (url.pathname === "/health") {
    return new Response(JSON.stringify({ status: "ok" }), {
      status: 200,
      headers: { "Content-Type": "application/json", ...corsHeaders() },
    });
  }

  // CORS preflight
  if (request.method === "OPTIONS") {
    return new Response(null, { status: 204, headers: corsHeaders() });
  }

  let targetUrl = null;

  // GET: /proxy?url=https://mp.weixin.qq.com/s/...
  if (url.pathname === "/proxy") {
    targetUrl = url.searchParams.get("url");

    // POST body fallback
    if (!targetUrl && request.method === "POST") {
      try {
        const body = await request.json();
        targetUrl = body.url;
      } catch {
        // ignore
      }
    }
  }

  if (!targetUrl) {
    return new Response(JSON.stringify({ error: "Missing 'url' parameter" }), {
      status: 400,
      headers: { "Content-Type": "application/json", ...corsHeaders() },
    });
  }

  if (!isAllowedDomain(targetUrl)) {
    return new Response(
      JSON.stringify({ error: "Domain not allowed", allowed_domains: ALLOWED_DOMAINS }),
      { status: 403, headers: { "Content-Type": "application/json", ...corsHeaders() } }
    );
  }

  try {
    // Forward select headers from client
    const headers = new Headers();
    const forwardHeaders = [
      "User-Agent",
      "Cookie",
      "Authorization",
      "Accept",
      "Accept-Language",
      "Accept-Encoding",
    ];
    for (const h of forwardHeaders) {
      const value = request.headers.get(h);
      if (value) {
        headers.set(h, value);
      }
    }

    // Default UA — same as we-mp-rss
    if (!headers.has("User-Agent")) {
      headers.set(
        "User-Agent",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
      );
    }

    // Add Chrome-like sec-* headers to avoid TLS fingerprinting detection
    if (!headers.has("Accept")) {
      headers.set("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7");
    }
    if (!headers.has("Accept-Language")) {
      headers.set("Accept-Language", "zh-CN,zh;q=0.9,en;q=0.8");
    }
    headers.set("sec-ch-ua", '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"');
    headers.set("sec-ch-ua-mobile", "?0");
    headers.set("sec-ch-ua-platform", '"Windows"');
    headers.set("sec-fetch-dest", "document");
    headers.set("sec-fetch-mode", "navigate");
    headers.set("sec-fetch-site", "none");
    headers.set("sec-fetch-user", "?1");
    headers.set("Upgrade-Insecure-Requests", "1");
    headers.set("Cache-Control", "max-age=0");

    const response = await fetch(targetUrl, {
      method: "GET",
      headers,
      redirect: "follow",
    });

    const contentType = response.headers.get("Content-Type") || "text/html";
    const isImage = contentType.includes("image/");

    const responseHeaders = new Headers(corsHeaders());
    responseHeaders.set("Content-Type", contentType);
    if (isImage) {
      responseHeaders.set("Cache-Control", "public, max-age=86400");
    }

    const body = isImage ? await response.arrayBuffer() : await response.text();

    return new Response(body, {
      status: response.status,
      headers: responseHeaders,
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown error";
    return new Response(
      JSON.stringify({ error: "Failed to fetch", message }),
      { status: 502, headers: { "Content-Type": "application/json", ...corsHeaders() } }
    );
  }
}

export default {
  fetch: handleRequest,
};
