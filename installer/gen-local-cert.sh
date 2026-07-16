#!/usr/bin/env bash
# Generate a local Root CA + a SAN server cert for the Home Hub, so it can be
# served over HTTPS on the LAN (required for a fully-installable PWA + service
# workers). Private keys stay local and are git-ignored; the CA (rootCA.crt) is
# published at /static/homehub-ca.crt for devices to trust once.
#
# Re-run after a restore or when the leaf cert (397 days) expires. If you change
# the LAN IP / hostnames, edit the SANs below and re-run.
set -e
LAN_IP="${LAN_IP:-192.168.1.9}"
CERTS=/home/kanishka/kk_works/LLMs/home-hub/certs
STATIC=/home/kanishka/kk_works/LLMs/home-hub/app/static
mkdir -p "$CERTS"; cd "$CERTS"; umask 077

if [ ! -f rootCA.crt ]; then
  openssl genrsa -out rootCA.key 4096
  openssl req -x509 -new -nodes -key rootCA.key -sha256 -days 3650 -out rootCA.crt \
    -subj "/O=HomeHub/CN=HomeHub Local CA"
fi

cat > server.cnf <<EOF
[req]
distinguished_name = dn
prompt = no
[dn]
O = HomeHub
CN = homehub.local
[v3]
subjectAltName = @alt
basicConstraints = CA:FALSE
keyUsage = digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
[alt]
DNS.1 = homehub.local
DNS.2 = localhost
DNS.3 = kanishka.local
IP.1  = ${LAN_IP}
IP.2  = 127.0.0.1
EOF

openssl genrsa -out homehub.key 2048
openssl req -new -key homehub.key -out homehub.csr -config server.cnf
openssl x509 -req -in homehub.csr -CA rootCA.crt -CAkey rootCA.key -CAcreateserial \
  -out homehub.crt -days 397 -sha256 -extfile server.cnf -extensions v3
chmod 600 *.key; chmod 644 *.crt
cp rootCA.crt "$STATIC/homehub-ca.crt"
echo "Done. Leaf cert:"
openssl x509 -in homehub.crt -noout -dates -ext subjectAltName
echo "Published CA -> $STATIC/homehub-ca.crt"
