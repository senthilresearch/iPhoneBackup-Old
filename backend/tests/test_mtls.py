"""Mutual TLS: the generated certificates verify, the agent presents them, and a server that requires them refuses clients without one."""
import http.server
import shutil
import ssl
import subprocess
import threading
from pathlib import Path

import httpx
import pytest

from mac_agent import tls

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(not shutil.which("openssl") or not shutil.which("bash"), reason="needs openssl")


@pytest.fixture()
def creds(tmp_path):
    d = tmp_path / "mtls"
    out = subprocess.run(["bash", str(ROOT / "deployment" / "make_client_cert.sh"), str(d)], capture_output=True, text=True, check=True)
    assert "ca.crt" in out.stdout
    return d


def test_script_makes_a_ca_and_a_client_certificate_the_ca_accepts(creds):
    assert {p.name for p in creds.iterdir()} == {"ca.crt", "ca.key", "client.crt", "client.key", "client.p12"}
    subprocess.run(["openssl", "verify", "-CAfile", str(creds / "ca.crt"), str(creds / "client.crt")], check=True, capture_output=True)
    assert oct((creds / "ca.key").stat().st_mode & 0o777) == "0o600"
    again = subprocess.run(["bash", str(ROOT / "deployment" / "make_client_cert.sh"), str(creds)], capture_output=True, text=True)
    assert again.returncode != 0  # never overwrites an existing CA


def test_agent_uses_the_certificate_only_for_https_and_only_when_present(creds, monkeypatch, tmp_path):
    monkeypatch.setenv("IPHONE_MTLS_DIR", str(tmp_path / "none"))
    assert tls.client_cert() is None and "client certificate" in tls.explain(Exception("tlsv13 alert certificate required"))
    monkeypatch.setenv("IPHONE_MTLS_DIR", str(creds))
    assert tls.client_cert() == (str(creds / "client.crt"), str(creds / "client.key"))
    assert "refused" in tls.explain(Exception("sslv3 alert bad certificate"))
    assert tls.explain(Exception("connection refused")) == ""
    tls.new_client("https://example.test", "t").close()  # loads the key pair
    tls.new_client("http://example.test", "t").close()   # plain http: no certificate presented


def test_server_requiring_a_client_certificate_accepts_ours_and_refuses_none(creds, monkeypatch, tmp_path):
    ca_key, ca_crt = creds / "ca.key", creds / "ca.crt"
    sk, scsr, scrt, ext = tmp_path / "s.key", tmp_path / "s.csr", tmp_path / "s.crt", tmp_path / "s.ext"
    ext.write_text("subjectAltName=DNS:localhost,IP:127.0.0.1\nextendedKeyUsage=serverAuth\n")
    run = lambda *a: subprocess.run(a, check=True, capture_output=True)  # noqa: E731
    run("openssl", "genrsa", "-out", str(sk), "2048")
    run("openssl", "req", "-new", "-key", str(sk), "-subj", "/CN=localhost", "-out", str(scsr))
    run("openssl", "x509", "-req", "-in", str(scsr), "-CA", str(ca_crt), "-CAkey", str(ca_key), "-CAcreateserial",
        "-days", "2", "-extfile", str(ext), "-out", str(scrt))

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.send_header("Content-Length", "2"); self.end_headers(); self.wfile.write(b"ok")
        log_message = lambda *a: None  # noqa: E731

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(scrt, sk)
    ctx.load_verify_locations(ca_crt)
    ctx.verify_mode = ssl.CERT_REQUIRED
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"https://localhost:{srv.server_address[1]}/"
    def trusting(cert_dir=None):
        c = ssl.create_default_context(cafile=str(ca_crt))  # trust our test CA for the server's certificate
        if cert_dir:
            c.load_cert_chain(str(cert_dir / "client.crt"), str(cert_dir / "client.key"))
        return c

    try:
        with httpx.Client(verify=trusting(creds)) as c:
            assert c.get(url).text == "ok"
        with pytest.raises(httpx.HTTPError):  # no client certificate: the handshake is refused
            with httpx.Client(verify=trusting()) as c:
                c.get(url)
        # the agent's own client (what the screen, the CLI and the Mac app use) presents the certificate by itself
        monkeypatch.setattr(tls.certifi, "where", lambda: str(ca_crt))  # make the test CA a trusted root for the server
        monkeypatch.setenv("IPHONE_MTLS_DIR", str(creds))
        with tls.new_client(url, "t") as c:
            assert c.get("/").text == "ok"
        monkeypatch.setenv("IPHONE_MTLS_DIR", str(tmp_path / "nothing-here"))
        with pytest.raises(httpx.HTTPError) as e, tls.new_client(url, "t") as c:
            c.get("/")
        assert "client certificate" in tls.explain(e.value)
        # a certificate from a different CA is refused too
        other = creds.parent / "other"
        subprocess.run(["bash", str(ROOT / "deployment" / "make_client_cert.sh"), str(other)], check=True, capture_output=True)
        with pytest.raises(httpx.HTTPError):
            with httpx.Client(verify=trusting(other)) as c:
                c.get(url)
    finally:
        srv.shutdown()


def test_mtls_is_opt_in_and_fails_closed():
    caddy = (ROOT / "deployment" / "docker" / "Caddyfile").read_text()
    assert "import {$MTLS_SNIPPET:/etc/caddy/no-mtls.conf}" in caddy
    mtls = (ROOT / "deployment" / "docker" / "mtls.conf").read_text()
    assert "require_and_verify" in mtls and "/mtls/ca.crt" in mtls
    assert all(line.lstrip().startswith("#") or not line.strip() for line in (ROOT / "deployment" / "docker" / "no-mtls.conf").read_text().splitlines())
