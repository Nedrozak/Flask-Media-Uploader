# Security Policy

## Supported versions

Security fixes are applied to the latest revision on the default branch.

## Reporting a vulnerability

Please use the repository hosting service's private security-advisory/reporting feature when available, rather than opening a public issue. Include the affected revision, reproduction steps, impact, and any suggested mitigation.

Do not include real user uploads, secrets, access tokens, or other sensitive data in a report.

## Security model

Share URLs are bearer capabilities: anyone who has a live share URL can access the files referenced by it until the share expires. Every file request re-validates the share token in Redis. Files are stored under random server-generated identifiers rather than user-provided filenames.

The application still requires normal deployment controls: HTTPS, a private Redis instance, a strong `FLASK_SECRET_KEY`, a trusted reverse proxy configuration, host validation, filesystem monitoring, backups appropriate to your environment, and externally enforced network controls. The included production Compose deployment additionally keeps the Docker socket out of Traefik and requires an edge bcrypt credential before a client can create uploads; bearer `/s/...` links remain intentionally public. If the administration panel is enabled, use a strong independently generated admin password hash and consider an additional network/identity control around `/admin` for Internet-facing deployments.

## Audit and privacy

Application audit events record a stable pseudonymous browser fingerprint, effective client IP addresses, User-Agent strings, accepted languages, route-level activity, upload/share lifecycle events, file metadata, and file SHA-256 digests. Raw share tokens and upload-session IDs are not logged; stable fingerprints are used for correlation instead. Production Traefik access logging is configured not to record request paths or query strings so bearer share URLs are not copied into proxy logs.

The browser correlation value is a random identifier stored in Flask's signed first-party session cookie; it is not authentication and can be reset by clearing cookies. Operators are responsible for disclosing this collection where required, controlling access to logs, and setting an appropriate retention policy. Correct `PROXY_FIX_*` configuration is required before treating the application-level client IP as authoritative behind a proxy.

Admin share history contains bearer tokens in Redis so authorized administrators can open/revoke still-live links. Registry collection is controlled by `SHARE_REGISTRY_ENABLED` and defaults to the value of `ADMIN_ENABLED`; with both settings disabled, share creation and access do not populate the admin registry. The cleanup worker prunes expired history indexes even if the admin UI is never opened. Those registry keys are not emitted to audit logs; Redis and admin access therefore remain security-sensitive and must not be exposed publicly.
