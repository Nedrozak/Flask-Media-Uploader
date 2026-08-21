# Render + Cloudflare R2 + Upstash deployment

This deployment mode keeps Flask on an ephemeral Render web service while
moving durable state to Upstash Redis and media bytes to a private Cloudflare
R2 bucket. Upload chunks and media downloads travel directly between the
browser and a Cloudflare Worker/R2, so large media does not transit Render.

## Cloudflare Worker

1. Create a private **Standard** R2 bucket (for example, `media-uploader`).
2. Edit `deploy/cloudflare-r2-worker/wrangler.toml` and set `bucket_name` to
   that bucket.
3. Generate a random 32-byte-or-longer secret. Set the same value in Render as
   `R2_DELIVERY_SECRET` and in the Worker as `DELIVERY_SECRET`.
4. From `deploy/cloudflare-r2-worker` run:

   ```sh
   npm install
   npx wrangler login
   npx wrangler secret put DELIVERY_SECRET
   npx wrangler deploy
   ```

5. Record the resulting `https://...workers.dev` origin as
   `R2_DELIVERY_BASE_URL`.
6. Add lifecycle rules for the `media/` prefix: expire objects after one day
   and abort incomplete multipart uploads after one day. The six-hour share
   token remains the access-control boundary; lifecycle deletion is only the
   storage cleanup safety net.

Keep the R2 bucket private. Do not enable its `r2.dev` public URL.

## Upstash

Create a Free Redis database and copy the TLS endpoint, port, and password.
This application uses the normal Redis protocol, so configure Render with:

```text
REDIS_URL=rediss://:PASSWORD@ENDPOINT:PORT
```

## Render Web Service

Create one **Free Web Service** from the Git repository and use the repository
Dockerfile. Set `/healthz` as the HTTP health-check path. The image reads
Render's `PORT` variable automatically.

Configure these environment variables (plus any upload limits you want to
override):

```text
FLASK_SECRET_KEY=<random 32-byte-or-longer secret>
REDIS_URL=rediss://:PASSWORD@ENDPOINT:PORT
STORAGE_BACKEND=r2
R2_ACCOUNT_ID=<Cloudflare account id>
R2_ACCESS_KEY_ID=<R2 scoped token access key>
R2_SECRET_ACCESS_KEY=<R2 scoped token secret key>
R2_BUCKET=media-uploader
R2_DELIVERY_BASE_URL=https://<worker>.workers.dev
R2_DELIVERY_SECRET=<same secret as Worker DELIVERY_SECRET>
R2_DELIVERY_URL_TTL_SECONDS=300
R2_UPLOAD_URL_TTL_SECONDS=600
UPLOAD_AUTH_ENABLED=true
UPLOAD_AUTH_USERNAME=uploader
UPLOAD_AUTH_PASSWORD_HASH=<Werkzeug password hash>
CHUNK_SIZE=5242880
MIN_FREE_DISK_BYTES=0
AUDIT_LOG_PATH=
AUDIT_LOG_CONSOLE=true
SESSION_COOKIE_SECURE=true
HSTS_MAX_AGE=31536000
HEALTH_DEPENDENCY_CHECK_INTERVAL_SECONDS=60
PROXY_FIX_X_FOR=1
PROXY_FIX_X_PROTO=1
PROXY_FIX_X_HOST=1
TOKEN_EXPIRY_SECONDS=21600
```

Generate `UPLOAD_AUTH_PASSWORD_HASH` with the existing
`media-uploader-admin-hash` command after installing the project. Generate the
Flask and Worker secrets independently.

After Render assigns the final service hostname, set `TRUSTED_HOSTS` to that
hostname (for example, `my-uploader.onrender.com`) and redeploy.
