# Portainer deployment with Tailscale Funnel

This deployment keeps Redis and uploaded files on the Docker host and exposes only the Flask web app through a dedicated Tailscale Funnel node. It does not use Cloudflare, R2, Render, Upstash, Caddy, router port forwarding, or the private `nedrozak.org` namespace.

Public traffic follows:

```text
Internet -> https://media-uploader.<tailnet>.ts.net -> Tailscale Funnel
         -> Tailscale sidecar -> http://127.0.0.1:5000 -> Flask
```

Redis is reachable only on the stack's internal Docker network. No container publishes a host port.

## Host directories

Create these before the first Portainer deployment:

```text
D:\MediaUploader\data
F:\docker\media-uploader\audit
F:\docker\media-uploader\redis
F:\docker\media-uploader\tailscale
```

The defaults in `compose.yaml` use those exact paths. Override them with Portainer stack environment variables if desired:

- `MEDIA_UPLOADER_UPLOADS_DIR`
- `MEDIA_UPLOADER_AUDIT_DIR`
- `MEDIA_UPLOADER_REDIS_DIR`
- `MEDIA_UPLOADER_TAILSCALE_DIR`

## Tailscale prerequisites

Tailscale Funnel requires MagicDNS, HTTPS certificates, and the `funnel` node attribute in the tailnet policy. For least privilege, give only this service the Funnel capability instead of enabling Funnel for every member-owned device. Add/merge the following entries in the Tailscale access-control policy:

```json
"tagOwners": {
  "tag:media-uploader-funnel": ["autogroup:admin"]
},
"nodeAttrs": [
  {
    "target": ["tag:media-uploader-funnel"],
    "attr": ["funnel"]
  }
]
```

If the policy already has `tagOwners` or `nodeAttrs`, merge these entries rather than replacing the existing sections.

Use a dedicated neutral node name such as `media-uploader`. Avoid private or identifying names because the Funnel HTTPS hostname is publicly resolvable and its certificate name is recorded in Certificate Transparency logs.

The expected public hostname is:

```text
media-uploader.<your-tailnet-name>.ts.net
```

Make sure no stale Tailscale machine already uses the same node name, or choose a different `TAILSCALE_HOSTNAME` and update `FUNNEL_HOST` to match.

Generate a one-off, non-ephemeral auth key for the first deployment and assign it the tag `tag:media-uploader-funnel`. If device approval is enabled, make the key pre-approved as well. Tagged auth keys are the recommended model for server devices. The Tailscale state directory is persisted and `TS_AUTH_ONCE=true`, so the key is only needed when the node has no saved identity. Tailscale automatically revokes a one-off auth key after it is used; after the node has joined successfully, remove the key value from the Portainer environment.

## Portainer stack

Create a Git-backed Portainer stack from this repository and use:

```text
Compose path: deploy/tailscale-funnel/compose.yaml
```

Set these stack environment variables before deployment:

```text
TAILSCALE_HOSTNAME=media-uploader
TS_AUTHKEY=<one-time Tailscale auth key>
FUNNEL_HOST=media-uploader.<your-tailnet-name>.ts.net
FLASK_SECRET_KEY=<at least 32 random characters>
UPLOAD_AUTH_USERNAME=uploader
UPLOAD_AUTH_PASSWORD_HASH=<Werkzeug password hash>
```

Recommended optional values:

```text
MIN_FREE_DISK_BYTES=10737418240
TOKEN_EXPIRY_SECONDS=21600
AUDIT_RETENTION_DAYS=14
```

To enable the app's administration panel, also set:

```text
ADMIN_ENABLED=true
ADMIN_USERNAME=admin
ADMIN_PASSWORD_HASH=<Werkzeug password hash>
```

Do not put plaintext passwords in the stack. Generate hashes with `media-uploader-admin-hash` from a local install or with the application image.

## Verify

After Portainer reports all services running, open a console in `media-uploader-tailscale` and run:

```sh
tailscale status
tailscale funnel status
```

The Funnel status should show the `https://...ts.net` URL and a proxy to `http://127.0.0.1:5000`.

Check the application through the public URL. `/` should request the uploader Basic Auth credentials. A generated `/s/<token>` share URL should remain accessible without the uploader credentials until its token expires.

The application uses `ProxyFix` for one trusted proxy hop because only the colocated Tailscale sidecar can reach the Flask process. HTTPS cookies and HSTS are enabled.

## Security notes

- There are no `ports:` mappings in this deployment.
- Redis is on an `internal: true` Docker network.
- The web app shares the Tailscale sidecar's network namespace so Funnel can proxy only to localhost, as required by Tailscale Serve/Funnel HTTP proxying.
- Funnel is public. Keep `UPLOAD_AUTH_ENABLED=true`, retain the app's rate limits, and use long random share tokens.
- Tailscale Funnel is currently beta and traffic is subject to non-configurable bandwidth limits.
- If the server or Tailscale node is offline, share links are offline too.
