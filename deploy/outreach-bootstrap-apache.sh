#!/bin/bash
# Run once on the VPS:
#   sudo bash /home/moondroo/outreach-bootstrap-apache.sh
set -euo pipefail

ENV_FILE=/var/www/outreach/.env
SITE=outreach.tools.moondroo.in
CONF_SRC=/home/moondroo/outreach.tools.moondroo.in.conf
CONF_DST=/etc/apache2/sites-available/${SITE}.conf

if [[ ! -f "$CONF_SRC" ]]; then
  echo "Missing $CONF_SRC" >&2
  exit 1
fi

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing $ENV_FILE — create it before enabling the site." >&2
  exit 1
fi

# Allow deploy user to edit .env later
chown moondroo:moondroo "$ENV_FILE"
chmod 600 "$ENV_FILE"

# Production CORS
if grep -q '^CORS_ORIGINS=' "$ENV_FILE"; then
  sed -i 's|^CORS_ORIGINS=.*|CORS_ORIGINS=https://outreach.tools.moondroo.in|' "$ENV_FILE"
else
  echo 'CORS_ORIGINS=https://outreach.tools.moondroo.in' >> "$ENV_FILE"
fi

a2enmod proxy proxy_http headers rewrite ssl
cp "$CONF_SRC" "$CONF_DST"
a2ensite "${SITE}.conf"
apache2ctl configtest
systemctl reload apache2

echo "HTTP vhost enabled. Next (DNS must already point here):"
echo "  sudo certbot --apache -d ${SITE}"
