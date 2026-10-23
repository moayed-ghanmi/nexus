import time
from collections import defaultdict
from typing import Tuple

# In-memory rate limiter tracking failed attempts per IP
# Structure: {ip: [timestamp1, timestamp2, ...]}
FAILED_ATTEMPTS = defaultdict(list)
LOCKOUT_DURATION_SECONDS = 300  # 5 minutes lockout
MAX_FAILED_ATTEMPTS = 5

def record_failed_attempt(ip: str):
    """Record a failed login attempt for the given IP address."""
    now = time.time()
    FAILED_ATTEMPTS[ip].append(now)

def check_ip_rate_limit(ip: str) -> Tuple[bool, int]:
    """
    Checks if an IP address is currently locked out due to excessive failed attempts.
    Returns: (is_allowed: bool, retry_after_seconds: int)
    """
    now = time.time()
    # Retain attempts within the last 5 minutes
    FAILED_ATTEMPTS[ip] = [t for t in FAILED_ATTEMPTS[ip] if now - t < LOCKOUT_DURATION_SECONDS]
    
    if len(FAILED_ATTEMPTS[ip]) >= MAX_FAILED_ATTEMPTS:
        oldest_relevant = FAILED_ATTEMPTS[ip][0]
        remaining = int(LOCKOUT_DURATION_SECONDS - (now - oldest_relevant))
        return False, max(1, remaining)
    
    return True, 0

def reset_failed_attempts(ip: str):
    """Clear failed attempts upon successful login."""
    if ip in FAILED_ATTEMPTS:
        del FAILED_ATTEMPTS[ip]
