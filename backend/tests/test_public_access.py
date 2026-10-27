"""Live check of the public HTTPS entry (Caddy + DuckDNS). Skipped unless PUBLIC_URL is set, e.g.

    PUBLIC_URL=https://beammedia.duckdns.org PUBLIC_TOKEN=<pairing token> [PUBLIC_MTLS=1] .venv/bin/python -m pytest tests/test_public_access.py -v

Run it from a machine OUTSIDE the home network (e.g. the MacBook on another network) to prove the router forward, the
certificate, and that only the upload API is published. The offline test below always runs.
"""
import os
import re
import socket
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest

from mac_agent.tls import new_client

URL = os.environ.get("PUBLIC_URL", "").rstrip("/")
TOKEN = os.environ.get("PUBLIC_TOKEN", "")
MTLS = os.environ.get("PUBLIC_MTLS", "") == "1"  # the PC requires a client certificate (docs/runbooks/REMOTE_ACCESS.md, mutual TLS)
live = pytest.mark.skipif(not URL, reason="set PUBLIC_URL (and PUBLIC_TOKEN) to test the public address")
ROOT = Path(__file__).resolve().parents[2]


def test_caddyfile_publishes_only_the_upload_api():
    text = (ROOT / "deployment" / "docker" / "Caddyfile").read_text()
    paths = re.findall(r"^\s*path\s+(.+)$", text, re.M)
    assert paths == ["/api/upload /api/upload/* /api/device"]
    assert "respond \"Not found\" 404" in text and "tls {" in text  # everything else 404, always HTTPS
    compose = (ROOT / "deployment" / "docker-compose.yml").read_text()
    assert 'profiles: ["public"]' in compose  # off unless the PC opts in


@live
def test_dns_and_port_reach_the_pc():
    host = urlsplit(URL).hostname
    port = urlsplit(URL).port or 443
    ip = socket.gethostbyname(host)  # raises if the DuckDNS name does not resolve
    with socket.create_connection((ip, port), timeout=8):
        pass  # fails when the router forward / firewall is missing, or Caddy is not running


@live
def test_certificate_is_valid_and_unknown_paths_are_hidden():
    with new_client(URL, "", 10) as c:  # verify=True: a bad/self-signed server certificate fails here; presents our client cert if present
        for path in ("/", "/index.html", "/api/library", "/api/pairing", "/api/settings", "/api/events"):
            assert c.get(URL + path).status_code == 404, path


@live
def test_upload_api_needs_the_token():
    with new_client(URL, "", 10) as c:
        assert c.get(URL + "/api/device").status_code == 401  # reachable, but refuses without the token
        assert c.get(URL + "/api/device", headers={"X-Token": "wrong"}).status_code == 401
        assert c.post(URL + "/api/upload/check", json={"client_id": "x", "items": []}).status_code == 401


@live
@pytest.mark.skipif(not TOKEN, reason="set PUBLIC_TOKEN to test authenticated calls")
def test_upload_api_works_with_the_token():
    with new_client(URL, TOKEN, 10) as c:
        assert c.get(URL + "/api/device").status_code == 200
        r = c.post(URL + "/api/upload/check", json={"client_id": "public-access-test", "items": []})
        assert r.status_code == 200 and r.json()["exists"] == []


@live
@pytest.mark.skipif(not MTLS, reason="set PUBLIC_MTLS=1 when the PC requires client certificates")
def test_mutual_tls_refuses_clients_without_a_certificate():
    with httpx.Client(timeout=10) as c:  # plain client: no certificate, so the TLS handshake itself is refused
        with pytest.raises(httpx.HTTPError):
            c.get(URL + "/api/device")
    with new_client(URL, "", 10) as c:  # with the Mac's certificate the connection is accepted (then 401: no token)
        assert c.get(URL + "/api/device").status_code == 401


@live
def test_plain_http_redirects_to_https():
    host = urlsplit(URL).hostname
    r = httpx.get(f"http://{host}/", timeout=10, follow_redirects=False)
    assert r.status_code in (301, 302, 307, 308) and r.headers["location"].startswith("https://")
