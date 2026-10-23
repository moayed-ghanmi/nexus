import hmac
import hashlib
import struct
import time
import base64
import secrets

def generate_secret(length: int = 16) -> str:
    """Generates a standard RFC 4648 Base32 secret key."""
    # 16 Base32 characters (A-Z, 2-7)
    valid_chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
    return "".join(secrets.choice(valid_chars) for _ in range(length))

def generate_totp(secret_base32: str, time_step: int = 30, digits: int = 6, for_time: float = None) -> str:
    """
    Computes RFC 6238 Time-based One-Time Password (TOTP).
    Default 30s step, 6 decimal digits, HMAC-SHA1.
    """
    if for_time is None:
        for_time = time.time()

    # Clean and pad base32 secret
    clean_secret = secret_base32.strip().replace(" ", "").upper()
    padding_needed = (8 - (len(clean_secret) % 8)) % 8
    clean_secret += "=" * padding_needed

    try:
        key = base64.b32decode(clean_secret)
    except Exception:
        return "000000"

    counter = int(for_time // time_step)
    msg = struct.pack(">Q", counter)
    h = hmac.new(key, msg, hashlib.sha1).digest()
    offset = h[-1] & 0x0F
    truncated = struct.unpack(">I", h[offset:offset+4])[0] & 0x7FFFFFFF
    code = str(truncated % (10 ** digits)).zfill(digits)
    return code

def verify_totp(secret_base32: str, candidate_code: str, window: int = 1, time_step: int = 30, digits: int = 6) -> bool:
    """
    Verifies candidate TOTP code allowing for clock drift within [-window, +window] steps.
    """
    if not secret_base32 or not candidate_code:
        return False

    candidate = candidate_code.strip()
    if len(candidate) != digits or not candidate.isdigit():
        return False

    now = time.time()
    for delta in range(-window, window + 1):
        test_time = now + (delta * time_step)
        if generate_totp(secret_base32, time_step=time_step, digits=digits, for_time=test_time) == candidate:
            return True

    return False

def get_provisioning_uri(secret_base32: str, email: str, issuer: str = "NEXUS KEYS") -> str:
    """Constructs the standard otpauth:// URL for Google Authenticator, Authy, etc."""
    safe_issuer = urllib_quote(issuer)
    safe_email = urllib_quote(email)
    return f"otpauth://totp/{safe_issuer}:{safe_email}?secret={secret_base32}&issuer={safe_issuer}&algorithm=SHA1&digits=6&period=30"

def urllib_quote(s: str) -> str:
    import urllib.parse
    return urllib.parse.quote(s)
