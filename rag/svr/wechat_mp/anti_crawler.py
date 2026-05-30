"""
Anti-crawler configuration — adapted from we-mp-rss driver/anti_crawler_config.py.
Provides browser fingerprint spoofing, JS injection scripts, and HTTP header config.
"""

import random
import uuid
from typing import Dict, Any

from .user_agent import UserAgentGenerator


class AntiCrawlerConfig:
    """Anti-crawler configuration manager."""

    def __init__(self):
        self._ua_generator = UserAgentGenerator()

    HEADERS = {
        'accept': [
            "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8",
        ],
        'accept_language': [
            "zh-CN,zh;q=0.9,en;q=0.8",
            "zh-CN,zh;q=0.8,zh-TW;q=0.7,zh-HK;q=0.5,en-US;q=0.3,en;q=0.2",
            "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        ],
        'cache_control': ["no-cache", "max-age=0", "no-store"],
    }

    def get_anti_crawler_config(self, mobile_mode: bool = False) -> Dict[str, Any]:
        config = {
            "user_agent": self._ua_generator.get_realistic_user_agent(mobile_mode),
            "viewport": {
                "width": random.randint(1200, 1920) if not mobile_mode else 720,
                "height": random.randint(800, 1080) if not mobile_mode else 1920,
                "device_scale_factor": random.choice([1, 1.25, 1.5, 2]),
            },
            "java_script_enabled": True,
            "ignore_https_errors": True,
            "bypass_csp": True,
            "extra_http_headers": self._get_http_headers(mobile_mode),
            "permissions": [],
        }
        if mobile_mode:
            config["extra_http_headers"].update({
                "X-Requested-With": "com.tencent.mm",
            })
        return config

    def _get_http_headers(self, mobile_mode: bool = False) -> Dict[str, str]:
        headers = {
            "Accept": random.choice(self.HEADERS['accept']),
            "Accept-Language": random.choice(self.HEADERS['accept_language']),
            "Accept-Encoding": "gzip, deflate, br",
            "Cache-Control": random.choice(self.HEADERS['cache_control']),
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
        }
        if mobile_mode:
            headers["X-Requested-With"] = "com.tencent.mm"
        return headers

    @staticmethod
    def get_init_script() -> str:
        """Returns the comprehensive anti-detection JS injection script."""
        return _ANTI_DETECTION_SCRIPT

    @staticmethod
    def get_behavior_script() -> str:
        return _BEHAVIOR_SCRIPT

    @staticmethod
    def get_viewport(mobile_mode: bool = False) -> Dict[str, int]:
        if mobile_mode:
            return {"width": 375, "height": 812}
        return {"width": random.randint(1200, 1920), "height": random.randint(800, 1080)}


# ============================================================
# Anti-detection JavaScript — injected into every page via add_init_script
# ============================================================

_ANTI_DETECTION_SCRIPT = """
// ============================================================
// Playwright anti-detection — adapted from we-mp-rss
// ============================================================

// 1. WebDriver detection (critical!)
delete Object.getPrototypeOf(navigator).webdriver;
Object.defineProperty(navigator, 'webdriver', {
    get: () => undefined,
    configurable: false,
    enumerable: true
});
const originalGetOwnPropertyDescriptor = Object.getOwnPropertyDescriptor;
Object.getOwnPropertyDescriptor = function(obj, prop) {
    if (prop === 'webdriver') return undefined;
    return originalGetOwnPropertyDescriptor.call(this, obj, prop);
};

// 2. Navigator properties
Object.defineProperty(navigator, 'plugins', {
    get: () => {
        const plugins = [
            Object.create(Plugin.prototype, {
                name: { value: 'PDF Viewer', enumerable: true },
                filename: { value: 'internal-pdf-viewer', enumerable: true },
                length: { value: 1, enumerable: true },
                item: { value: (i) => i === 0 ? { type: 'application/pdf', suffixes: 'pdf' } : null },
            }),
            Object.create(Plugin.prototype, {
                name: { value: 'Chrome PDF Plugin', enumerable: true },
                filename: { value: 'internal-pdf-viewer', enumerable: true },
                length: { value: 1, enumerable: true },
                item: { value: (i) => i === 0 ? { type: 'application/x-google-chrome-pdf', suffixes: 'pdf' } : null },
            }),
        ];
        plugins.length = 2;
        plugins.item = (i) => plugins[i] || null;
        plugins.refresh = () => {};
        return plugins;
    },
    configurable: false, enumerable: true
});

Object.defineProperty(navigator, 'mimeTypes', {
    get: () => {
        const mimeTypes = [
            { type: 'application/pdf', suffixes: 'pdf', enabledPlugin: { name: 'PDF Viewer' } },
            { type: 'application/x-google-chrome-pdf', suffixes: 'pdf', enabledPlugin: { name: 'Chrome PDF Plugin' } },
        ];
        mimeTypes.length = 2;
        mimeTypes.item = (i) => mimeTypes[i] || null;
        return mimeTypes;
    },
    configurable: false, enumerable: true
});

Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en-US', 'en'], configurable: false });
Object.defineProperty(navigator, 'platform', { get: () => 'Win32', configurable: false });
Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8, configurable: false });
Object.defineProperty(navigator, 'deviceMemory', { get: () => 8, configurable: false });

// 3. Chrome object
if (!window.chrome) {
    window.chrome = {
        app: { isInstalled: false },
        runtime: {
            connect: () => ({ onDisconnect: { addListener: () => {} }, postMessage: () => {} }),
            sendMessage: () => {},
        },
        csi: () => ({ onloadT: Date.now(), pageT: Date.now(), startE: Date.now(), tran: 15 }),
        loadTimes: () => ({
            requestTime: Date.now() / 1000, startLoadTime: Date.now() / 1000,
            commitLoadTime: Date.now() / 1000, finishDocumentLoadTime: Date.now() / 1000,
            finishLoadTime: Date.now() / 1000, firstPaintTime: Date.now() / 1000,
            navigationType: 'Other', wasFetchedViaSpdy: true, wasNpnNegotiated: true,
            npnNegotiatedProtocol: 'h2', connectionInfo: 'h2',
        }),
    };
}

// 4. Permissions API
const origQuery = navigator.permissions.query.bind(navigator.permissions);
navigator.permissions.query = (params) => {
    if (params.name === 'notifications') return Promise.resolve({ state: Notification.permission, onchange: null });
    return origQuery(params);
};

// 5. iframe contentWindow fix
const origCW = Object.getOwnPropertyDescriptor(HTMLIFrameElement.prototype, 'contentWindow');
Object.defineProperty(HTMLIFrameElement.prototype, 'contentWindow', {
    get: function() {
        const win = origCW.get.call(this);
        if (win) { try { Object.defineProperty(win.navigator, 'webdriver', { get: () => undefined }); } catch (e) {} }
        return win;
    }
});

// 6. WebGL fingerprint spoofing
const getParam = WebGLRenderingContext.prototype.getParameter;
WebGLRenderingContext.prototype.getParameter = function(p) {
    if (p === 37445) return 'Google Inc. (NVIDIA)';
    if (p === 37446) return 'ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)';
    if (p === 3379) return 16384;
    return getParam.apply(this, arguments);
};

// 7. WebRTC disable (prevent IP leak)
if (window.RTCPeerConnection) { window.RTCPeerConnection = undefined; }
if (window.webkitRTCPeerConnection) { window.webkitRTCPeerConnection = undefined; }

// 8. Battery API spoof
if (navigator.getBattery) {
    navigator.getBattery = () => Promise.resolve({
        charging: true, chargingTime: 0, dischargingTime: Infinity, level: 1,
        addEventListener: () => {}, removeEventListener: () => {},
    });
}

// 9. Automation traces removal
const propsToDelete = [
    '__playwright', '__puppeteer', '__selenium', '__webdriver_evaluate',
    '__selenium_evaluate', '__fxdriver_evaluate', '__driver_unwrapped',
    '__webdriver_unwrapped', '__selenium_unwrapped', '__fxdriver_unwrapped',
    '__webdriver_script_function', '__webdriver_script_func', '__webdriver_script_fn',
    '__nightmare', '__phantomas', '__bugzilla',
    'cdc_adoQpoasnfa76pfcZLmcfl_Array', 'cdc_adoQpoasnfa76pfcZLmcfl_Promise',
    'cdc_adoQpoasnfa76pfcZLmcfl_Symbol', 'cdc_adoQpoasnfa76pfcZLmcfl_JSON',
    'cdc_adoQpoasnfa76pfcZLmcfl_Object', 'callPhantom', '_phantom',
    'nightmare', 'domAutomation', 'domAutomationController',
];
propsToDelete.forEach(prop => { try { delete window[prop]; delete document[prop]; } catch (e) {} });

// 10. Screen properties
Object.defineProperty(screen, 'colorDepth', { get: () => 24 });
Object.defineProperty(screen, 'pixelDepth', { get: () => 24 });

// 11. Console protection
const origDebug = console.debug;
console.debug = function() {
    if (arguments[0] && arguments[0].toString().includes('webdriver')) return;
    return origDebug.apply(console, arguments);
};

// 12. Timezone emulation
const origDTF = Intl.DateTimeFormat;
Intl.DateTimeFormat = function(locale, options) {
    if (options && options.timeZone) options.timeZone = 'Asia/Shanghai';
    return new origDTF(locale, options);
};
Intl.DateTimeFormat.prototype = origDTF.prototype;

console.log('[Anti-Detection] Enhanced fingerprint protection enabled');
"""

_BEHAVIOR_SCRIPT = """
// Mouse click delay randomization
const origAddEv = EventTarget.prototype.addEventListener;
EventTarget.prototype.addEventListener = function(type, listener, options) {
    if (type === 'click') {
        const wrapped = function(...args) {
            setTimeout(() => listener.apply(this, args), Math.random() * 100 + 50);
        };
        return origAddEv.call(this, type, wrapped, options);
    }
    return origAddEv.call(this, type, listener, options);
};
"""

# Global instance
_anti_crawler_config = AntiCrawlerConfig()


def get_anti_crawler_config(mobile_mode: bool = False) -> Dict[str, Any]:
    return _anti_crawler_config.get_anti_crawler_config(mobile_mode)


def get_init_script() -> str:
    return AntiCrawlerConfig.get_init_script()


def get_behavior_script() -> str:
    return AntiCrawlerConfig.get_behavior_script()
