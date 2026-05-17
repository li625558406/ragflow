import base64
import json
import urllib.request
import urllib.error
import ssl

# ===== 配置 =====
BASE_URL = "http://localhost:9222"
EMAIL = "lg18629285296@163.com"
PASSWORD = "12345678"

RSA_PUBLIC_KEY = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEArq9XTUSeYr2+N1h3Afl/z8Dse/2yD0ZGrKwx+EEEcdsBLca9Ynmx3nIB5obmLlSfmskLpBo0UACBmB5rEjBp2Q2f3AG3Hjd4B+gNCG6BDaawuDlgANIhGnaTLrIqWrrcm4EMzJOnAOI1fgzJRsOOUEfaS318Eq9OVO3apEyCCt0lOQK6PuksduOjVxtltDav+guVAA068NrPYmRNabVKRNLJpL8w4D44sfth5RvZ3q9t+6RTArpEtc5sh5ChzvqPOzKGMXW83C95TxmXqpbK6olN4RevSfVjEAgCydH6HN6OhtOQEcnrU97r9H0iZOWwbw3pVrZiUkuRD1R56Wzs2wIDAQAB
-----END PUBLIC KEY-----"""

from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_v1_5

def rsa_encrypt(password: str) -> str:
    # Step 1: UTF-8 → base64（和前端 utf8ToBase64 一致）
    b64_pwd = base64.b64encode(password.encode("utf-8")).decode("ascii")
    # Step 2: RSA PKCS1-v1.5 加密
    key = RSA.import_key(RSA_PUBLIC_KEY)
    cipher = PKCS1_v1_5.new(key)
    encrypted = cipher.encrypt(b64_pwd.encode("utf-8"))
    # Step 3: base64 输出
    return base64.b64encode(encrypted).decode("ascii")

def login():
    encrypted_pwd = rsa_encrypt(PASSWORD)
    print(f"[1/3] 加密密码(前50): {encrypted_pwd[:50]}...")

    body = json.dumps({
        "email": EMAIL,
        "password": encrypted_pwd,
    }).encode("utf-8")

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    req = urllib.request.Request(
        f"{BASE_URL}/api/v1/auth/login",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, context=ctx, timeout=10)
        print(f"[2/3] 登录状态码: {resp.status}")
        auth_header = resp.headers.get("Authorization", "")
        print(f"[3/3] Authorization: {auth_header[:80]}...")
        token = auth_header.replace("Bearer ", "").strip()
        return token
    except urllib.error.HTTPError as e:
        print(f"❌ 登录失败 HTTP {e.code}: {e.read().decode()}")
        return None
    except Exception as e:
        print(f"❌ 登录异常: {e}")
        return None

def list_agents(token):
    if not token:
        return None
    req = urllib.request.Request(
        f"{BASE_URL}/api/v1/agents?page_size=100",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        resp = urllib.request.urlopen(req, context=ctx, timeout=10)
        data = json.loads(resp.read().decode("utf-8"))
        print(f"\n📋 Agent 列表:")
        print(json.dumps(data, indent=2, ensure_ascii=False)[:2000])
        return data
    except Exception as e:
        print(f"❌ 获取 Agent 列表失败: {e}")
        return None

if __name__ == "__main__":
    print("🔐 开始登录...\n")
    token = login()
    if token:
        list_agents(token)