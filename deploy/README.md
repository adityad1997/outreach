# Outreach production deploy (Contabo / Apache)

Push-to-deploy: laptop → bare git repo → `/var/www/outreach` → Docker Compose.
HTTPS is terminated by **Apache** on the host; the app binds `127.0.0.1:8000`.

| Item | Value |
|---|---|
| Hostname | `outreach.tools.moondroo.in` |
| Bare repo | `/var/git/outreach.git` |
| Work tree | `/var/www/outreach` |
| Compose file | `docker-compose.prod.yml` |
| Postgres volume | `outreach_pgdata` (Docker, external) |
| Git remote (laptop) | `contabo7eu` → `contabo7eu:/var/git/outreach.git` |

## One-time VPS setup

```bash
# Docker without sudo (once)
sudo usermod -aG docker moondroo
# log out of SSH completely, then: ssh contabo7eu
docker ps

sudo mkdir -p /var/git /var/www/outreach
sudo chown -R moondroo:moondroo /var/git /var/www/outreach

git init --bare /var/git/outreach.git
git -C /var/git/outreach.git symbolic-ref HEAD refs/heads/main

docker volume create outreach_pgdata

# After this repo is checked out or you have the file locally:
cp /path/to/deploy/post-receive /var/git/outreach.git/hooks/post-receive
chmod +x /var/git/outreach.git/hooks/post-receive
```

### `.env` (never commit)

```bash
cp .env.example /var/www/outreach/.env
# edit at least:
#   CADDY_HOST=
#   BASE_URL=https://outreach.tools.moondroo.in
#   GOOGLE_CLIENT_ID=...
#   GOOGLE_CLIENT_SECRET=...
#   CORS_ORIGINS=https://outreach.tools.moondroo.in
#   QUICKLY_TELEMETRY=false   # optional
```

### Apache vhost (HTTP first, then Certbot)

Enable modules if needed:

```bash
sudo a2enmod proxy proxy_http headers rewrite ssl
```

Example `/etc/apache2/sites-available/outreach.tools.moondroo.in.conf`:

```apache
<VirtualHost *:80>
    ServerName outreach.tools.moondroo.in

    ProxyPreserveHost On
    RequestHeader set X-Forwarded-Proto "http"
    ProxyPass / http://127.0.0.1:8000/
    ProxyPassReverse / http://127.0.0.1:8000/
</VirtualHost>
```

```bash
sudo a2ensite outreach.tools.moondroo.in.conf
sudo apache2ctl configtest && sudo systemctl reload apache2
sudo certbot --apache -d outreach.tools.moondroo.in
```

After Certbot, ensure forwarded proto is HTTPS (Certbot often adds a 443 vhost). Prefer:

```apache
RequestHeader set X-Forwarded-Proto "https"
```

on the SSL vhost.

## Deploy from laptop

```bash
git remote add contabo7eu contabo7eu:/var/git/outreach.git   # once
git push contabo7eu main
```

The `post-receive` hook checks out `main` into `/var/www/outreach` and runs:

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

It will **not** overwrite `.env`.

## Google OAuth redirect URIs

Add both in Google Cloud Console for this host:

- `https://outreach.tools.moondroo.in/oauth/google/callback` (Gmail inboxes)
- `https://outreach.tools.moondroo.in/oauth/app/google/callback` (app login)
