#!/usr/bin/env python3
"""Test zjk.ggzyfw.fujian.gov.cn listing page."""
import requests
from bs4 import BeautifulSoup
import warnings
warnings.filterwarnings("ignore")

r = requests.get("https://zjk.ggzyfw.fujian.gov.cn/zffg/notice.html",
                 verify=False, timeout=15,
                 headers={"User-Agent": "Mozilla/5.0"})
print("STATUS:", r.status_code, "LEN:", len(r.text))

soup = BeautifulSoup(r.text, "lxml")
lis = soup.find_all("li")
print("Total li:", len(lis))

for li in lis:
    a = li.find("a", href=True)
    if a and "/zffg/" in a.get("href", ""):
        href = a["href"][:60]
        title = li.get_text(strip=True)[:60]
        print(f"  {href}  |  {title}")
