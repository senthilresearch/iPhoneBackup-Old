"""Client certificate for servers that require mutual TLS (see make_client_cert.sh and docs/runbooks/REMOTE_ACCESS.md).

The key pair is looked up in IPHONE_MTLS_DIR (default ~/.iphone-backup-mtls) as client.crt + client.key and is presented only
to https servers. Without those files nothing changes.
"""
import os
import ssl
from pathlib import Path

import certifi
import httpx


def mtls_dir() -> Path:
    return Path(os.environ.get("IPHONE_MTLS_DIR") or Path.home() / ".iphone-backup-mtls")


def client_cert() -> tuple[str, str] | None:
    d = mtls_dir()
    crt, key = d / "client.crt", d / "client.key"
    return (str(crt), str(key)) if crt.is_file() and key.is_file() else None


def new_client(server: str, token: str, timeout: float = 60) -> httpx.Client:
    server = server.rstrip("/")
    cert = client_cert() if server.lower().startswith("https://") else None
    verify: ssl.SSLContext | bool = True
    if cert:  # the same trust store httpx uses by default, plus our client key pair
        verify = ssl.create_default_context(cafile=certifi.where())
        verify.load_cert_chain(*cert)
    return httpx.Client(base_url=server, headers={"X-Token": token}, timeout=timeout, verify=verify)


def explain(e: Exception) -> str:
    """A hint when a TLS failure probably means the server wants a client certificate we don't have (or it was refused)."""
    text = str(e).lower()
    if any(w in text for w in ("certificate required", "handshake failure", "bad certificate", "unknown ca", "alert")):
        if client_cert() is None:
            return " The server may require a client certificate: run ./deployment/make_client_cert.sh on this Mac (see docs/runbooks/REMOTE_ACCESS.md)."
        return " The server refused this Mac's client certificate: check that the PC has the matching ca.crt."
    return ""
