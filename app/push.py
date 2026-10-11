"""Web Push, the standard every modern browser supports (Chrome, Edge, Firefox, Safari on iOS 16.4+ when
the site is added to the home screen). No Firebase or third-party account: the server signs each push with
its own VAPID key (RFC 8292) and encrypts the message for that one browser (RFC 8291, aes128gcm per
RFC 8188), then posts it to the push service the browser gave us.

The key pair is made once and kept in the database (or set VAPID_PRIVATE_KEY), so subscriptions keep
working across restarts and deploys.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import struct
import time
from urllib.parse import urlsplit

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .config import settings
from .db import AppSetting, SessionLocal

log = logging.getLogger("urbanshield.push")


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def b64u_dec(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _raw_public(key: ec.EllipticCurvePublicKey) -> bytes:
    return key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


# ---------- VAPID key ----------
_key: ec.EllipticCurvePrivateKey | None = None


def vapid_key() -> ec.EllipticCurvePrivateKey:
    global _key
    if _key is not None:
        return _key
    pem = os.environ.get("VAPID_PRIVATE_KEY", "").replace("\\n", "\n").strip()
    if not pem:
        with SessionLocal() as s:
            row = s.get(AppSetting, "vapid_private_pem")
            if row is None:
                k = ec.generate_private_key(ec.SECP256R1())
                pem = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                      serialization.NoEncryption()).decode()
                s.add(AppSetting(key="vapid_private_pem", value=pem))
                s.commit()
                log.info("Created a new Web Push (VAPID) key pair")
            else:
                pem = row.value
    _key = serialization.load_pem_private_key(pem.encode(), password=None)
    return _key


def public_key() -> str:
    """The applicationServerKey the browser needs to subscribe (base64url, uncompressed P-256 point)."""
    return b64u(_raw_public(vapid_key().public_key()))


def vapid_jwt(endpoint: str, ttl_s: int = 12 * 3600) -> str:
    u = urlsplit(endpoint)
    claims = {"aud": f"{u.scheme}://{u.netloc}", "exp": int(time.time()) + ttl_s,
              "sub": f"mailto:{settings.contact_email or 'alerts@urbanshield.app'}"}
    head = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    body = b64u(json.dumps(claims, separators=(",", ":")).encode())
    der = vapid_key().sign(f"{head}.{body}".encode(), ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    return f"{head}.{body}.{b64u(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"


# ---------- message encryption (RFC 8291 + RFC 8188, aes128gcm) ----------
def _hkdf(salt: bytes, ikm: bytes, info: bytes, n: int) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=n, salt=salt, info=info).derive(ikm)


def encrypt(payload: bytes, p256dh: str, auth: str, *, salt: bytes | None = None,
            server_key: ec.EllipticCurvePrivateKey | None = None) -> bytes:
    ua_pub_raw = b64u_dec(p256dh)
    auth_secret = b64u_dec(auth)
    ua_pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_pub_raw)
    as_key = server_key or ec.generate_private_key(ec.SECP256R1())   # fresh key per message
    as_pub_raw = _raw_public(as_key.public_key())
    shared = as_key.exchange(ec.ECDH(), ua_pub)
    ikm = _hkdf(auth_secret, shared, b"WebPush: info\x00" + ua_pub_raw + as_pub_raw, 32)
    salt = salt or os.urandom(16)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    rs = 4096
    if len(payload) > rs - 17 - 86:
        raise ValueError("push payload too large")
    cipher = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)   # 0x02 marks the last (only) record
    return salt + struct.pack("!IB", rs, len(as_pub_raw)) + as_pub_raw + cipher


# ---------- sending ----------
class Gone(Exception):
    """The browser unsubscribed or the subscription expired (404/410): delete it."""


_client: httpx.AsyncClient | None = None


async def send(endpoint: str, p256dh: str, auth: str, message: dict, ttl: int = 3600, urgency: str = "normal") -> int:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=15)
    body = encrypt(json.dumps(message, ensure_ascii=False).encode(), p256dh, auth)
    r = await _client.post(endpoint, content=body, headers={
        "TTL": str(ttl), "Urgency": urgency, "Content-Encoding": "aes128gcm",
        "Content-Type": "application/octet-stream",
        "Authorization": f"vapid t={vapid_jwt(endpoint)}, k={public_key()}",
    })
    if r.status_code in (404, 410):
        raise Gone(r.status_code)
    if r.status_code >= 400:
        raise RuntimeError(f"push service said {r.status_code}: {r.text[:200]}")
    return r.status_code
