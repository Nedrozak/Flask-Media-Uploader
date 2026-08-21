# Oracle Cloud single-VM deployment

This is the intended small-production topology for Media Uploader:

- one OCI VM;
- one public hostname;
- inbound TCP 80/443 only (plus tightly restricted SSH or OCI Bastion for administration);
- Traefik as the only Internet-facing container;
- Flask and Redis reachable only through Docker networks;
- a dedicated block volume mounted at `/srv/media-uploader` for uploads, Redis AOF, audit logs, and ACME state.

The upload UI and upload API are protected by Traefik bcrypt Basic Auth. Bearer share URLs under `/s/` remain public. `/admin/`, if enabled in the application, has both the outer Basic Auth gate and the application's own admin login.

## Host preparation summary

1. Create the VM, public IP, DNS record, and network rules.
2. Attach and mount a dedicated data volume at `/srv/media-uploader`.
3. Install Docker Engine and the Docker Compose plugin.
4. Clone the repository and create `.env` from `.env.example`.
5. Run `sudo ./deploy/oci/prepare-host.sh /srv/media-uploader`.
6. Create `/srv/media-uploader/secrets/upload-users.htpasswd` with `htpasswd -B`.
7. Render `/srv/media-uploader/traefik/dynamic.yml` with `deploy/traefik/render-dynamic-config.sh`.
8. Run `sudo docker compose config`, review the output, then `sudo docker compose up -d --build`.
9. Verify HTTP redirects, HTTPS certificate issuance, `/healthz`, uploader authentication, and that a generated `/s/...` link works without uploader credentials.

Do not routinely back up expired upload payloads unless that retention behavior is intentional. Back up source/configuration and secrets securely; treat Redis and application audit data as sensitive.
