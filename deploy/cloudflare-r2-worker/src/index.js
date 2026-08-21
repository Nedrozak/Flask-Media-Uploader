const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, HEAD, PUT, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, Range, If-Match, If-None-Match, If-Modified-Since, If-Unmodified-Since",
  "Access-Control-Expose-Headers": "Accept-Ranges, Content-Length, Content-Range, Content-Disposition, Content-Type, ETag",
  "Access-Control-Max-Age": "86400",
};

function addCors(headers) {
  for (const [name, value] of Object.entries(CORS_HEADERS)) headers.set(name, value);
}

function text(message, status) {
  const headers = new Headers({
    "Content-Type": "text/plain; charset=utf-8",
    "Cache-Control": "private, no-store, max-age=0",
    "X-Content-Type-Options": "nosniff",
  });
  addCors(headers);
  return new Response(message, { status, headers });
}

function json(value, status = 200) {
  const headers = new Headers({
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "private, no-store, max-age=0",
    "X-Content-Type-Options": "nosniff",
  });
  addCors(headers);
  return new Response(JSON.stringify(value), { status, headers });
}

function encodeFilename(value) {
  return encodeURIComponent(value).replace(/[!'()*]/g, (char) =>
    `%${char.charCodeAt(0).toString(16).toUpperCase()}`,
  );
}

function contentDisposition(disposition, filename) {
  const fallback = filename
    .replace(/[^\x20-\x7E]/g, "_")
    .replace(/["\\]/g, "_")
    .slice(0, 180);
  return `${disposition}; filename="${fallback}"; filename*=UTF-8''${encodeFilename(filename)}`;
}

async function hmacHex(secret, message) {
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey(
    "raw",
    encoder.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const signature = await crypto.subtle.sign("HMAC", key, encoder.encode(message));
  return [...new Uint8Array(signature)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

function timingSafeEqual(a, b) {
  if (a.length !== b.length) return false;
  let difference = 0;
  for (let index = 0; index < a.length; index += 1) {
    difference |= a.charCodeAt(index) ^ b.charCodeAt(index);
  }
  return difference === 0;
}

async function validSignature(secret, message, suppliedSignature) {
  if (!/^[0-9a-f]{64}$/.test(suppliedSignature)) return false;
  const expected = await hmacHex(secret, message);
  return timingSafeEqual(expected, suppliedSignature);
}

function rangeHeaders(headers, object) {
  headers.set("Accept-Ranges", "bytes");
  if (!object.range) {
    headers.set("Content-Length", String(object.size));
    return 200;
  }

  let offset = object.range.offset;
  let length = object.range.length;
  if (offset === undefined && object.range.suffix !== undefined) {
    length = Math.min(object.range.suffix, object.size);
    offset = object.size - length;
  }
  if (offset === undefined) offset = 0;
  if (length === undefined) length = object.size - offset;

  const end = offset + length - 1;
  headers.set("Content-Length", String(length));
  headers.set("Content-Range", `bytes ${offset}-${end}/${object.size}`);
  return 206;
}

async function uploadPart(request, env, url, key) {
  const uploadId = url.searchParams.get("uploadId") || "";
  const part = Number.parseInt(url.searchParams.get("part") || "", 10);
  const expectedSize = Number.parseInt(url.searchParams.get("size") || "", 10);
  const expires = Number.parseInt(url.searchParams.get("exp") || "", 10);
  const suppliedSignature = (url.searchParams.get("sig") || "").toLowerCase();
  const now = Math.floor(Date.now() / 1000);

  if (
    !key.startsWith("media/") ||
    !uploadId ||
    !Number.isSafeInteger(part) ||
    part < 1 ||
    part > 10000 ||
    !Number.isSafeInteger(expectedSize) ||
    expectedSize < 1 ||
    expectedSize > 100 * 1024 * 1024 ||
    !Number.isSafeInteger(expires) ||
    expires <= now
  ) {
    return text("Upload link expired or invalid", 403);
  }

  const message = `upload\n${key}\n${uploadId}\n${part}\n${expectedSize}\n${expires}`;
  if (!(await validSignature(env.DELIVERY_SECRET, message, suppliedSignature))) {
    return text("Invalid upload link", 403);
  }
  if (!request.body) return text("Missing request body", 400);

  let body = request.body;
  const contentLength = request.headers.get("Content-Length");
  if (contentLength !== null) {
    const received = Number.parseInt(contentLength, 10);
    if (!Number.isSafeInteger(received) || received !== expectedSize) {
      return text("Upload part size does not match signed metadata", 400);
    }
  } else {
    // Browser Blob uploads normally have Content-Length. This bounded fallback
    // still verifies size for clients/proxies that omit it; application chunks
    // are 5 MiB, comfortably below the Worker memory/request-body limits.
    const buffered = await request.arrayBuffer();
    if (buffered.byteLength !== expectedSize) {
      return text("Upload part size does not match signed metadata", 400);
    }
    body = buffered;
  }

  try {
    const multipart = env.MEDIA_BUCKET.resumeMultipartUpload(key, uploadId);
    const uploaded = await multipart.uploadPart(part, body);
    return json({ partNumber: uploaded.partNumber, etag: uploaded.etag });
  } catch (error) {
    console.error("R2 multipart uploadPart failed", error);
    return text("R2 could not store this upload part", 502);
  }
}

async function downloadObject(request, env, url, key) {
  const expRaw = url.searchParams.get("exp") || "";
  const disposition = url.searchParams.get("disposition") || "";
  const filename = url.searchParams.get("name") || "";
  const suppliedSignature = (url.searchParams.get("sig") || "").toLowerCase();
  const expires = Number.parseInt(expRaw, 10);
  const now = Math.floor(Date.now() / 1000);

  if (!key.startsWith("media/") || !Number.isSafeInteger(expires) || expires <= now) {
    return text("Link expired or invalid", 403);
  }
  if (disposition !== "inline" && disposition !== "attachment") {
    return text("Invalid disposition", 403);
  }
  if (!filename || filename.length > 255) return text("Invalid link", 403);
  const message = `${key}\n${expires}\n${disposition}\n${filename}`;
  if (!(await validSignature(env.DELIVERY_SECRET, message, suppliedSignature))) {
    return text("Invalid link", 403);
  }

  if (request.method === "HEAD") {
    const object = await env.MEDIA_BUCKET.head(key);
    if (!object) return text("Not found", 404);
    const headers = new Headers();
    object.writeHttpMetadata(headers);
    headers.set("ETag", object.httpEtag);
    headers.set("Accept-Ranges", "bytes");
    headers.set("Content-Length", String(object.size));
    headers.set("Content-Disposition", contentDisposition(disposition, filename));
    headers.set("Cache-Control", "private, no-store, max-age=0");
    headers.set("X-Content-Type-Options", "nosniff");
    addCors(headers);
    return new Response(null, { status: 200, headers });
  }

  const object = await env.MEDIA_BUCKET.get(key, {
    onlyIf: request.headers,
    range: request.headers,
  });
  if (!object) return text("Not found", 404);
  if (!object.body) return text("Precondition failed", 412);

  const headers = new Headers();
  object.writeHttpMetadata(headers);
  headers.set("ETag", object.httpEtag);
  headers.set("Content-Disposition", contentDisposition(disposition, filename));
  headers.set("Cache-Control", "private, no-store, max-age=0");
  headers.set("X-Content-Type-Options", "nosniff");
  addCors(headers);
  const status = rangeHeaders(headers, object);
  return new Response(object.body, { status, headers });
}

export default {
  async fetch(request, env) {
    if (request.method === "OPTIONS") {
      const headers = new Headers();
      addCors(headers);
      return new Response(null, { status: 204, headers });
    }
    if (!env.DELIVERY_SECRET) return text("Worker is not configured", 503);

    const url = new URL(request.url);
    let key;
    try {
      key = decodeURIComponent(url.pathname.replace(/^\/+/, ""));
    } catch (_) {
      return text("Invalid object key", 400);
    }

    if (request.method === "PUT" && url.searchParams.get("action") === "upload") {
      return uploadPart(request, env, url, key);
    }
    if (request.method === "GET" || request.method === "HEAD") {
      return downloadObject(request, env, url, key);
    }
    return text("Method not allowed", 405);
  },
};
