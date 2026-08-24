FROM tailscale/tailscale:stable

COPY deploy/tailscale-funnel/serve.json /config/serve.json
