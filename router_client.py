#!/usr/bin/env python3
"""
ZyXEL router client.

Session-managed wrapper around the ZyXEL DAL API.
Login flow: fetch RSA pubkey → AES-CBC encrypt payload → RSA-encrypt AES key → POST /UserLogin.
Responses may themselves be AES-encrypted using the same session key.

This module is a class-based refactor of the original router.py script.
"""

import base64
import json
import os
import urllib3
import requests
from Crypto.Cipher import AES, PKCS1_v1_5
from Crypto.PublicKey import RSA
from Crypto.Util.Padding import pad, unpad

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class RouterClient:
    """Session-managed client for the ZyXEL DAL API.

    Maintains a login session and re-authenticates automatically on expiry.
    All network calls are synchronous; use asyncio.to_thread() when calling
    from async contexts.
    """

    def __init__(self, base_url: str, username: str, password: str) -> None:
        self.base_url = base_url
        self.username = username
        self.password = password
        self._http = requests.Session()
        self._http.verify = False
        self._session_key: str | None = None
        self._aes_key: bytes | None = None

    # ── HTTP helpers ─────────────────────────────────────────────────────────

    def _get(self, path: str, **kw):
        return self._http.get(self.base_url + path, **kw)

    def _post(self, path: str, **kw):
        return self._http.post(self.base_url + path, **kw)

    # ── Crypto (mirrors router JS exactly) ───────────────────────────────────
    #
    # JS: u = WordArray.random(32).toString(Base64)   // IV  — 32 raw bytes as b64
    #     p = WordArray.random(32).toString(Base64)   // key — 32 raw bytes as b64
    #     AES.encrypt(plaintext, Base64.parse(p), {iv: Base64.parse(u), CBC, PKCS7})
    #     JSEncrypt.encrypt(p)   // RSA-encrypts the base64 KEY STRING
    #
    # AES-256-CBC: key = 32 raw bytes; IV = first 16 bytes of the 32-byte iv_raw.

    def _fetch_rsa_key(self) -> RSA.RsaKey:
        r = self._get("/getRSAPublickKey")
        return RSA.import_key(r.json()["RSAPublicKey"])

    def _aes_rsa_encrypt(self, plaintext: str, rsa_key) -> tuple[dict, bytes]:
        aes_key_raw = os.urandom(32)
        iv_raw = os.urandom(32)
        aes_key_b64 = base64.b64encode(aes_key_raw).decode()
        iv_b64 = base64.b64encode(iv_raw).decode()

        cipher_aes = AES.new(aes_key_raw, AES.MODE_CBC, iv_raw[:16])
        ciphertext = cipher_aes.encrypt(pad(plaintext.encode(), AES.block_size))

        cipher_rsa = PKCS1_v1_5.new(rsa_key)
        encrypted_key = cipher_rsa.encrypt(aes_key_b64.encode())

        payload = {
            "content": base64.b64encode(ciphertext).decode(),
            "key": base64.b64encode(encrypted_key).decode(),
            "iv": iv_b64,
        }
        return payload, aes_key_raw

    def _aes_decrypt(self, encrypted_b64: str, aes_key_raw: bytes, iv_b64: str) -> dict:
        iv_raw = base64.b64decode(iv_b64)[:16]
        ct = base64.b64decode(encrypted_b64)
        cipher = AES.new(aes_key_raw, AES.MODE_CBC, iv_raw)
        return json.loads(unpad(cipher.decrypt(ct), AES.block_size).decode())

    def _maybe_decrypt(self, data: dict, aes_key_raw: bytes) -> dict:
        """Decrypt response if it's wrapped in {content, iv}."""
        if isinstance(data, dict) and set(data) == {"content", "iv"}:
            return self._aes_decrypt(data["content"], aes_key_raw, data["iv"])
        return data

    # ── Session management ────────────────────────────────────────────────────

    def _login(self) -> tuple[str, bytes]:
        rsa_key = self._fetch_rsa_key()
        login_obj = {
            "Input_Account": self.username,
            "Input_Passwd": base64.b64encode(self.password.encode()).decode(),
            "Input_RandomCode": "",
            "currLang": "en",
            "RememberPassword": "0",
            "SHA512_password": False,
        }
        payload, aes_key_raw = self._aes_rsa_encrypt(json.dumps(login_obj), rsa_key)
        r = self._post("/UserLogin", json=payload)
        if r.status_code != 200 or not r.text.strip().startswith("{"):
            raise RuntimeError(f"Login failed: HTTP {r.status_code} — {r.text[:200]}")

        resp = r.json()
        decrypted = self._maybe_decrypt(resp, aes_key_raw)
        sk = decrypted.get("sessionkey") or decrypted.get("SessionKey")
        if not sk:
            raise RuntimeError(f"No session key in login response: {decrypted}")
        return sk, aes_key_raw

    def _ensure_session(self) -> None:
        if self._session_key is None:
            self._session_key, self._aes_key = self._login()

    def _invalidate_session(self) -> None:
        self._session_key = None
        self._aes_key = None

    # ── DAL API ───────────────────────────────────────────────────────────────

    def dal_get(self, oid: str) -> dict | list:
        """GET a DAL object. Re-authenticates once on session expiry."""
        for attempt in range(2):
            self._ensure_session()
            r = self._get(
                f"/cgi-bin/DAL?oid={oid}&DalGetOneObject=y",
                headers={"CSRFToken": self._session_key},
            )
            text = r.text.strip()
            if not (text.startswith("{") or text.startswith("[")):
                if attempt == 0 and r.status_code in (401, 403):
                    self._invalidate_session()
                    continue
                raise RuntimeError(f"Non-JSON response for GET {oid}: {text[:200]}")
            data = r.json()
            return self._maybe_decrypt(data, self._aes_key)
        raise RuntimeError(f"Failed to GET {oid} after re-authentication")

    def dal_post(self, oid: str, body: dict) -> dict:
        """POST to a DAL object (write operation). Re-authenticates once on session expiry."""
        for attempt in range(2):
            self._ensure_session()
            r = self._post(
                f"/cgi-bin/DAL?oid={oid}",
                json=body,
                headers={"CSRFToken": self._session_key},
            )
            text = r.text.strip()
            if not text.startswith("{"):
                if attempt == 0 and r.status_code in (401, 403):
                    self._invalidate_session()
                    continue
                raise RuntimeError(f"Non-JSON response for POST {oid}: {text[:200]}")
            data = r.json()
            return self._maybe_decrypt(data, self._aes_key)
        raise RuntimeError(f"Failed to POST {oid} after re-authentication")
