#!/usr/bin/env python3
"""
ZyXEL router client.
Login flow: fetch RSA pubkey → AES-CBC encrypt payload → RSA-encrypt AES key → POST /UserLogin
"""

import base64
import json
import os
import urllib3
import requests
from Crypto.Cipher import AES, PKCS1_v1_5
from Crypto.PublicKey import RSA
from Crypto.Util.Padding import pad

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE     = os.environ.get("ROUTER_BASE_URL", "https://192.168.1.1")
USERNAME = os.environ.get("ROUTER_USERNAME", "admin")
PASSWORD = os.environ["ROUTER_PASSWORD"]

s = requests.Session()
s.verify = False


def get(path, **kw):
    return s.get(BASE + path, **kw)

def post(path, **kw):
    return s.post(BASE + path, **kw)


def fetch_rsa_key():
    r = get("/getRSAPublickKey")
    data = r.json()
    pem = data["RSAPublicKey"]
    print(f"[+] RSA public key fetched ({len(pem)} chars)")
    return RSA.import_key(pem)


def aes_rsa_encrypt(plaintext: str, rsa_key) -> dict:
    """
    Mirrors the router's AesRsaEncrypt JS function exactly:

    JS:  u = WordArray.random(32).toString(Base64)   // IV  — 32 bytes, stored as b64 string
         p = WordArray.random(32).toString(Base64)   // key — 32 bytes, stored as b64 string
         AES.encrypt(plaintext, Base64.parse(p), {iv: Base64.parse(u), CBC, PKCS7})
         JSEncrypt.encrypt(p)   // RSA-encrypts the base64 KEY STRING, not raw bytes

    CryptoJS uses the full 32 bytes for AES-256 key; for IV it uses the
    first 16 bytes of the decoded value (128-bit block size).
    """
    aes_key_raw = os.urandom(32)                          # 32 raw bytes
    iv_raw      = os.urandom(32)                          # 32 raw bytes (JS generates 32)

    aes_key_b64 = base64.b64encode(aes_key_raw).decode()  # base64 string stored in localStorage
    iv_b64      = base64.b64encode(iv_raw).decode()        # base64 string sent in iv field

    # AES-256-CBC: key = 32 bytes, iv = first 16 bytes of iv_raw
    cipher_aes = AES.new(aes_key_raw, AES.MODE_CBC, iv_raw[:16])
    ciphertext = cipher_aes.encrypt(pad(plaintext.encode(), AES.block_size))

    # RSA-PKCS1v1.5 encrypts the base64 KEY STRING (as bytes), matching JSEncrypt behaviour
    cipher_rsa    = PKCS1_v1_5.new(rsa_key)
    encrypted_key = cipher_rsa.encrypt(aes_key_b64.encode())

    payload = {
        "content": base64.b64encode(ciphertext).decode(),
        "key":     base64.b64encode(encrypted_key).decode(),
        "iv":      iv_b64,
    }
    return payload, aes_key_raw  # return raw key for response decryption


def aes_decrypt(encrypted_b64: str, aes_key_raw: bytes, iv_b64: str) -> dict:
    """
    Mirrors AesDecrypt(content, key_b64, iv_b64).
    key: raw bytes (32); iv: base64-decoded, first 16 bytes used.
    """
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import unpad
    iv_raw  = base64.b64decode(iv_b64)[:16]
    ct      = base64.b64decode(encrypted_b64)
    cipher  = AES.new(aes_key_raw, AES.MODE_CBC, iv_raw)
    return json.loads(unpad(cipher.decrypt(ct), AES.block_size).decode())


def login():
    rsa_key = fetch_rsa_key()

    login_obj = {
        "Input_Account":    USERNAME,
        "Input_Passwd":     base64.b64encode(PASSWORD.encode()).decode(),
        "Input_RandomCode": "",
        "currLang":         "en",
        "RememberPassword": "0",
        "SHA512_password":  False,
    }

    payload, aes_key_raw = aes_rsa_encrypt(json.dumps(login_obj), rsa_key)
    print(f"[+] Encrypted payload prepared")

    r = post("/UserLogin", json=payload)
    print(f"[+] POST /UserLogin → {r.status_code}")

    if r.status_code != 200 or not r.text.strip().startswith("{"):
        print(f"[!] Unexpected response: {r.text[:300]}")
        return None, None

    resp = r.json()
    # Response is itself AES-encrypted (same key, new iv from response)
    if "content" in resp and "iv" in resp:
        print("[+] Response is encrypted — decrypting…")
        try:
            decrypted = aes_decrypt(resp["content"], aes_key_raw, resp["iv"])
            print(f"[+] Decrypted response: {json.dumps(decrypted, indent=2)[:400]}")
            sk = decrypted.get("sessionkey") or decrypted.get("SessionKey")
            if sk:
                print(f"[+] Session key: {sk}")
                return sk, aes_key_raw
        except Exception as e:
            print(f"[!] Decryption failed: {e}")
            return None, None
    else:
        sk = resp.get("sessionkey")
        if sk:
            return sk, aes_key_raw
        print(f"[!] Login response: {resp}")
    return None, None


def dal_get(oid, session_key, aes_key_raw, label=None):
    """GET a DAL object, decrypting the response if needed."""
    r = get(f"/cgi-bin/DAL?oid={oid}&DalGetOneObject=y",
            headers={"CSRFToken": session_key})
    label = label or oid
    if not (r.text.strip().startswith("{") or r.text.strip().startswith("[")):
        print(f"\n── {label} ── {r.status_code} (non-JSON)")
        return None
    try:
        data = r.json()
        # Decrypt if response is wrapped in {content, iv}
        if isinstance(data, dict) and "content" in data and "iv" in data and len(data) == 2:
            data = aes_decrypt(data["content"], aes_key_raw, data["iv"])
        print(f"\n── {label} ──")
        print(json.dumps(data, indent=2)[:2000])
        return data
    except Exception as e:
        print(f"\n── {label} ── error: {e} | raw: {r.text[:200]}")
        return None


def dump_status(sk, aes_key):
    print(f"\n{'='*60}")
    print("Router status")
    print('='*60)
    dal_get("status",     sk, aes_key, "General status")
    dal_get("wan",        sk, aes_key, "WAN")
    dal_get("lan",        sk, aes_key, "LAN")
    dal_get("lanhosts",   sk, aes_key, "LAN hosts / DHCP leases")
    dal_get("wlan",       sk, aes_key, "WLAN")
    dal_get("dns",        sk, aes_key, "DNS")
    dal_get("ethctl",     sk, aes_key, "Ethernet port status")


if __name__ == "__main__":
    sk, aes_key = login()
    if sk:
        dump_status(sk, aes_key)
    else:
        print("\n[!] Login failed — check credentials or encryption format")
