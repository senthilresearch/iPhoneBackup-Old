#!/usr/bin/env bash
# Creates the mutual-TLS credentials for the public HTTPS entry. Run it ON THE MAC (the keys never leave it).
#   ./make_client_cert.sh [dir]        default dir: ~/.iphone-backup-mtls
# Produces: ca.key (keep secret, offline), ca.crt (the ONLY file to copy to the PC), client.key + client.crt (the Mac agent
# and the Mac app pick these up automatically), client.p12 (for importing into Keychain/Safari/iPhone if ever needed).
set -euo pipefail
D="${1:-$HOME/.iphone-backup-mtls}"
mkdir -p "$D" && chmod 700 "$D"
cd "$D"
if [ -e ca.key ]; then echo "Already exists in $D (ca.key). Delete the folder first to start over." >&2; exit 1; fi
ext() { local f; f=$(mktemp); printf '%s\n' "$@" > "$f"; echo "$f"; }

# CA: signs client certificates; the PC only needs ca.crt to recognise them.
openssl genrsa -out ca.key 4096 2>/dev/null
openssl req -x509 -new -key ca.key -sha256 -days 1825 -subj "/CN=iPhone Backup client CA" \
  -out ca.crt -addext "basicConstraints=critical,CA:TRUE" 2>/dev/null || \
  openssl req -x509 -new -key ca.key -sha256 -days 1825 -subj "/CN=iPhone Backup client CA" -out ca.crt

# Client certificate for this Mac.
openssl genrsa -out client.key 2048 2>/dev/null
openssl req -new -key client.key -subj "/CN=iPhone Backup Mac" -out client.csr
E=$(ext "basicConstraints=CA:FALSE" "keyUsage=digitalSignature" "extendedKeyUsage=clientAuth")
openssl x509 -req -in client.csr -CA ca.crt -CAkey ca.key -CAcreateserial -sha256 -days 730 -extfile "$E" -out client.crt 2>/dev/null
rm -f "$E" client.csr ca.srl
P12PASS=$(openssl rand -hex 8)
openssl pkcs12 -export -inkey client.key -in client.crt -certfile ca.crt -passout "pass:$P12PASS" -out client.p12 2>/dev/null
chmod 600 ca.key client.key client.p12
openssl verify -CAfile ca.crt client.crt >/dev/null

cat <<MSG
Created in $D:
  ca.crt      copy THIS to the PC:  <repo>/config/mtls/ca.crt   (it is public; it cannot create certificates)
  ca.key      never copy anywhere; whoever has it can mint certificates that the PC will accept
  client.crt / client.key   used automatically by the Mac agent and the Mac app (https servers only)
  client.p12  password: $P12PASS  (only needed to import the certificate somewhere else)
Client certificate expires in 2 years (run this script again in a fresh folder to renew, then replace ca.crt on the PC).
MSG
