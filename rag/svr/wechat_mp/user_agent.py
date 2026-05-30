"""
User-Agent generator — adapted from we-mp-rss driver/user_agent.py.
Supports multi-browser, dynamic version numbers, realistic device simulation.
"""

import random


class UserAgentGenerator:
    """User-Agent generator supporting mobile and desktop multi-browser types."""

    def __init__(self):
        self.mobile_browser_weights = {
            'chrome': 0.45, 'safari': 0.30, 'firefox': 0.10,
            'edge': 0.08, 'opera': 0.05, 'qq': 0.02,
        }
        self.desktop_browser_weights = {
            'chrome': 0.65, 'edge': 0.12, 'firefox': 0.08,
            'safari': 0.08, 'opera': 0.05, 'qq': 0.02,
        }

    def get_realistic_user_agent(self, mobile_mode: bool = True) -> str:
        if mobile_mode:
            return self._generate_mobile_ua()
        return self._generate_desktop_ua()

    def _generate_mobile_ua(self) -> str:
        browser_type = random.choices(
            list(self.mobile_browser_weights.keys()),
            weights=list(self.mobile_browser_weights.values()))[0]
        generators = {
            'chrome': self._chrome_mobile, 'safari': self._safari_mobile,
            'firefox': self._firefox_mobile, 'edge': self._edge_mobile,
            'opera': self._opera_mobile, 'qq': self._qq_mobile,
        }
        return generators[browser_type]()

    def _generate_desktop_ua(self) -> str:
        browser_type = random.choices(
            list(self.desktop_browser_weights.keys()),
            weights=list(self.desktop_browser_weights.values()))[0]
        generators = {
            'chrome': self._chrome_desktop, 'edge': self._edge_desktop,
            'firefox': self._firefox_desktop, 'safari': self._safari_desktop,
            'opera': self._opera_desktop, 'qq': self._qq_desktop,
        }
        return generators[browser_type]()

    def _chrome_ver(self): return f"{random.randint(110,125)}.{random.randint(0,9)}.{random.randint(4000,6500)}.{random.randint(0,200)}"
    def _firefox_ver(self): return str(random.randint(110, 125))
    def _safari_ver(self): return f"{random.randint(15,17)}.{random.randint(0,6)}"
    def _edge_ver(self): return f"{random.randint(110,125)}.{random.randint(0,9)}.{random.randint(1000,2500)}.{random.randint(0,100)}"
    def _opera_ver(self): m = random.randint(90, 110); return f"{m}.{random.randint(0,9)}.{random.randint(4000,5500)}.{m-13}"
    def _android_ver(self): return random.choices(['10','11','12','13','14'], weights=[0.15,0.20,0.30,0.25,0.10])[0]
    def _ios_ver(self): return random.choices(['15_0','15_5','16_0','16_5','17_0','17_2','17_4'], weights=[0.10,0.15,0.15,0.20,0.20,0.15,0.05])[0]
    def _win_ver(self): return random.choices(['Windows NT 10.0; Win64; x64','Windows NT 10.0; WOW64'], weights=[0.85,0.15])[0]
    def _macos_ver(self): return random.choices(['10_15_7','11_0','12_0','13_0','14_0'], weights=[0.25,0.15,0.20,0.25,0.15])[0]
    def _linux_distro(self): return random.choice(['X11; Linux x86_64','X11; Ubuntu; Linux x86_64'])
    def _android_device(self):
        return random.choice([
            'SM-G991B','SM-S908B','Mi 11','Mi 13','Redmi K60',
            'Pixel 7','Pixel 8','OnePlus 11','OPPO Find X3','Vivo X80',
        ])

    # --- Mobile UAs ---
    def _chrome_mobile(self): return f"Mozilla/5.0 (Linux; Android {self._android_ver()}; {self._android_device()}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{self._chrome_ver()} Mobile Safari/537.36"
    def _safari_mobile(self): return f"Mozilla/5.0 (iPhone; CPU iPhone OS {self._ios_ver()} like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/{self._safari_ver()} Mobile/15E148 Safari/604.1"
    def _firefox_mobile(self): v = self._firefox_ver(); return f"Mozilla/5.0 (Android {self._android_ver()}; Mobile; rv:{v}.0) Gecko/{v}.0 Firefox/{v}.0"
    def _edge_mobile(self): return f"Mozilla/5.0 (Linux; Android {self._android_ver()}; {self._android_device()}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{self._chrome_ver()} Mobile Safari/537.36 EdgA/{self._edge_ver()}"
    def _opera_mobile(self): return f"Mozilla/5.0 (Linux; Android {self._android_ver()}; {self._android_device()}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{self._chrome_ver()} Mobile Safari/537.36 OPR/{self._opera_ver()}"
    def _qq_mobile(self): return f"Mozilla/5.0 (Linux; Android {self._android_ver()}; {self._android_device()}) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/{self._chrome_ver()} MQQBrowser/{random.randint(13,15)}.{random.randint(0,5)}.{random.randint(3000,3500)} Mobile Safari/537.36"

    # --- Desktop UAs ---
    def _chrome_desktop(self): return f"Mozilla/5.0 ({self._win_ver()}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{self._chrome_ver()} Safari/537.36"
    def _edge_desktop(self): return f"Mozilla/5.0 ({self._win_ver()}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{self._chrome_ver()} Safari/537.36 Edg/{self._edge_ver()}"
    def _firefox_desktop(self): v = self._firefox_ver(); return f"Mozilla/5.0 ({self._win_ver()}; rv:{v}.0) Gecko/20100101 Firefox/{v}.0"
    def _safari_desktop(self): return f"Mozilla/5.0 (Macintosh; Intel Mac OS X {self._macos_ver()}) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/{self._safari_ver()} Safari/605.1.15"
    def _opera_desktop(self): return f"Mozilla/5.0 ({self._win_ver()}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{self._chrome_ver()} Safari/537.36 OPR/{self._opera_ver()}"
    def _qq_desktop(self): return f"Mozilla/5.0 ({self._win_ver()}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{self._chrome_ver()} Safari/537.36 QQBrowser/{random.randint(13,15)}.{random.randint(0,5)}.{random.randint(5000,5500)}"


_ua_generator = UserAgentGenerator()


def get_user_agent(mobile_mode: bool = False) -> str:
    return _ua_generator.get_realistic_user_agent(mobile_mode)
