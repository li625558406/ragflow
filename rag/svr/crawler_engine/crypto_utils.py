"""
Cryptography utilities for encrypted API crawlers.

Supported algorithms:
- SM4-ECB (Chinese national standard, key: 90bdd291004611ef87fc52540023e781)
- AES-256-CBC (key: EB444973714E4A40876CE66BE45D5930, IV: B5A8904209931867)
- MD5 portal-sign (secret: B3978D054A72A7002063637CCDF6B2E5)
"""

import base64
import hashlib
import json
import logging
from typing import Any, Dict, Optional

try:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import unpad as _pkcs7_unpad
    _CRYPTO_AVAILABLE = True
except ImportError:
    _CRYPTO_AVAILABLE = False
    AES = None

try:
    from gmssl.sm4 import CryptSM4, SM4_DECRYPT, SM4_ENCRYPT
    _SM4_AVAILABLE = True
except ImportError:
    _SM4_AVAILABLE = False
    CryptSM4 = None


# ---------------------------------------------------------------------------
# SM4 (Chinese National Standard)
# ---------------------------------------------------------------------------

def sm4_ecb_decrypt(ciphertext: bytes, key: bytes) -> bytes:
    """Decrypt SM4-ECB ciphertext. Returns plaintext or empty bytes on failure."""
    if not _SM4_AVAILABLE:
        raise ImportError("gmssl is required for SM4 decryption")
    try:
        c = CryptSM4()
        c.set_key(key, SM4_DECRYPT)
        return c.crypt_ecb(ciphertext)
    except Exception as e:
        logging.warning("SM4-ECB decrypt failed: %s", e)
        return b""


def sm4_ecb_encrypt(plaintext: bytes, key: bytes) -> bytes:
    """Encrypt plaintext with SM4-ECB."""
    if not _SM4_AVAILABLE:
        raise ImportError("gmssl is required for SM4 encryption")
    c = CryptSM4()
    c.set_key(key, SM4_ENCRYPT)
    return c.crypt_ecb(plaintext)


# ---------------------------------------------------------------------------
# AES-256-CBC
# ---------------------------------------------------------------------------

def aes_cbc_decrypt(ciphertext: bytes, key: bytes, iv: bytes) -> bytes:
    """Decrypt AES-256-CBC ciphertext with PKCS7 unpadding."""
    if not _CRYPTO_AVAILABLE:
        raise ImportError("pycryptodome is required for AES decryption")
    try:
        cipher = AES.new(key, AES.MODE_CBC, iv)
        return _pkcs7_unpad(cipher.decrypt(ciphertext), AES.block_size)
    except Exception as e:
        logging.warning("AES-CBC decrypt failed: %s", e)
        return b""


def aes_cbc_encrypt(plaintext: bytes, key: bytes, iv: bytes) -> bytes:
    """Encrypt plaintext with AES-256-CBC and PKCS7 padding."""
    if not _CRYPTO_AVAILABLE:
        raise ImportError("pycryptodome is required for AES encryption")
    from Crypto.Util.Padding import pad as _pkcs7_pad
    cipher = AES.new(key, AES.MODE_CBC, iv)
    return cipher.encrypt(_pkcs7_pad(plaintext, AES.block_size))


# ---------------------------------------------------------------------------
# MD5 Portal Sign
# ---------------------------------------------------------------------------

def md5_sign(params: Dict[str, Any], secret: str) -> str:
    """Generate MD5 portal signature.

    Algorithm: MD5(secret + sorted_key_value_concat).lower()

    Sorted-key concatenation: sort param keys alphabetically, concatenate
    key=value pairs without separators.
    """
    sorted_keys = sorted(k for k in params.keys() if params[k] is not None)
    concat = "".join(f"{k}={params[k]}" for k in sorted_keys)
    sign_str = secret + concat
    return hashlib.md5(sign_str.encode("utf-8")).hexdigest().lower()


# ---------------------------------------------------------------------------
# Convenience: hex/base64 helpers
# ---------------------------------------------------------------------------

def hex_to_bytes(s: str) -> bytes:
    return bytes.fromhex(s)


def b64_to_bytes(s: str) -> bytes:
    return base64.b64decode(s)


def bytes_to_b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")
