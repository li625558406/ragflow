"""
Cookie expiry management — adapted from we-mp-rss driver/cookies.py.
"""

import logging
import time


def expire(cookies):
    """Extract the earliest cookie expiry from a list of cookie dicts."""
    if not isinstance(cookies, (list, dict)):
        raise TypeError("cookies must be list or dict")

    priority_cookies = ['slave_sid', 'slave_user', 'bizuin', 'uin', 'pass_ticket']

    if isinstance(cookies, list):
        for priority_name in priority_cookies:
            for cookie in cookies:
                if not isinstance(cookie, dict):
                    continue
                if cookie.get('name') == priority_name:
                    expiry = _extract_expiry(cookie)
                    if expiry:
                        return expiry

        for cookie in cookies:
            if not isinstance(cookie, dict):
                continue
            expiry = _extract_expiry(cookie)
            if expiry:
                return expiry

    # Default: 2 hour session
    logging.getLogger(__name__).warning("Could not extract valid expiry from cookies, using 2h default")
    default_expiry = time.time() + 7200
    return {
        'expiry_timestamp': default_expiry,
        'remaining_seconds': 7200,
        'expiry_time': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(default_expiry)),
    }


def _extract_expiry(cookie: dict):
    for field in ('expires', 'expiry', 'expire'):
        if field not in cookie:
            continue
        try:
            val = cookie[field]
            if isinstance(val, (int, float)):
                expiry_time = float(val)
            elif isinstance(val, str) and val.isdigit():
                expiry_time = float(val)
            else:
                continue
            remaining = expiry_time - time.time()
            if remaining > 0:
                return {
                    'expiry_timestamp': expiry_time,
                    'remaining_seconds': int(remaining),
                    'expiry_time': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(expiry_time)),
                }
        except (ValueError, TypeError):
            continue
    return None
