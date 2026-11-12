# Using an existing reverse proxy

Choose blank hostname during installation to avoid starting bundled Caddy on occupied ports. Configure your existing HTTPS reverse proxy to forward to `http://127.0.0.1:5678` with WebSocket upgrade support.

Edit `/opt/waqaar-n8n/.env` as root:

```dotenv
N8N_HOST=n8n.example.com
N8N_PROTOCOL=https
N8N_PUBLIC_URL=https://n8n.example.com
N8N_PROXY_HOPS=1
N8N_SECURE_COOKIE=true
N8N_BIND=127.0.0.1
COMPOSE_PROFILES=
```

Use the actual hostname and the number of trusted proxies in your deployment. Keep the bridge and database off public ports. Apply with:

```bash
cd /opt/waqaar-n8n
sudo docker compose up -d
```

Example Nginx location inside your existing HTTPS server block:

```nginx
location / {
    proxy_pass http://127.0.0.1:5678;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 300s;
}
```

Reuse your proxy's existing certificate configuration. Test it before reloading. Do not disable TLS verification or expose the unconfigured n8n owner-signup screen to other people.
