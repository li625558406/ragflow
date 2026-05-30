"""
本地独立测试脚本 —— 不依赖 ragflow2 数据库，不启动服务。

用法:
    # 1. 扫码登录（会自动打开浏览器）
    python rag/svr/wechat_mp/standalone_test.py login

    # 2. 查看登录状态
    python rag/svr/wechat_mp/standalone_test.py status

    # 3. 搜索公众号
    python rag/svr/wechat_mp/standalone_test.py search --kw InfoQ

    # 4. 采集文章（faker_id 从上一步获取）
    python rag/svr/wechat_mp/standalone_test.py collect --fakeid gh_xxx --title "InfoQ" --max-page 1 --content

    # 5. 完整流程：登录 + 搜索 + 采集
    python rag/svr/wechat_mp/standalone_test.py full --kw InfoQ --max-page 1 --content

依赖: pip install playwright requests beautifulsoup4
      playwright install chromium
"""

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

# Ensure the ragflow2 root is on path so 'from rag.svr.wechat_mp' works
PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# ═══════════════════════════════════════════════════════════════
# Monkey-patch token module — use local JSON file instead of DB
# Must happen BEFORE any wechat_mp module import
# ═══════════════════════════════════════════════════════════════

_LOCAL_TOKEN_FILE = str(Path(__file__).resolve().parent / "local_token.json")


def _local_token_get(key, default="", tenant_id=""):
    try:
        with open(_LOCAL_TOKEN_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return str(data.get(key, default))
    except (FileNotFoundError, json.JSONDecodeError):
        return str(default)


def _local_token_set(data, tenant_id="", ext_data=None):
    with open(_LOCAL_TOKEN_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"[token] 已保存到 {_LOCAL_TOKEN_FILE}")


# Inject before imports
import rag.svr.wechat_mp.token as token_mod
token_mod.get = _local_token_get
token_mod.set_token = _local_token_set

# Now safe to import the rest
from rag.svr.wechat_mp.wx_login import WxLogin
from rag.svr.wechat_mp.gather import WxGather
from rag.svr.wechat_mp.mps_api import MpsApi

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("standalone_test")


# ═══════════════════════════════════════════════════════════════

def cmd_login():
    """扫码登录，保存 token 到本地 JSON 文件"""
    print("=" * 60)
    print("  微信公众平台扫码登录")
    print("=" * 60)

    wx = WxLogin(tenant_id="local")

    async def _do():
        await wx.login_via_qrcode()
        # login_via_qrcode 内部会调用 token.set_token 保存
        if wx.has_login():
            print("\n✅ 登录成功！")
            ext = wx._ext_data or {}
            name = ext.get("wx_app_name", "未知")
            print(f"   公众号: {name}")
            session = wx._session or {}
            print(f"   Token: {(session.get('token', '') or '')[:30]}...")
            expiry = (session.get("expiry") or {})
            print(f"   过期时间: {expiry.get('expiry_time', '未知')}")
        else:
            print("\n❌ 登录失败或超时")

    import asyncio
    asyncio.run(_do())


def cmd_status():
    """查看当前登录状态"""
    if not os.path.exists(_LOCAL_TOKEN_FILE):
        print("❌ 未登录，请先运行 login 命令")
        return

    with open(_LOCAL_TOKEN_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    token = data.get("token", "")
    expiry = data.get("expiry", {})

    if isinstance(expiry, dict):
        remaining = expiry.get("remaining_seconds", 0)
        exp_time = expiry.get("expiry_time", "")
    else:
        remaining = 0
        exp_time = ""

    print("=" * 60)
    print("  登录状态")
    print("=" * 60)
    print(f"  Token:      {(token or '')[:40]}..." if token else "  Token:      空")
    print(f"  过期时间:    {exp_time}")
    print(f"  剩余时间:    {remaining} 秒" if remaining else "")
    print(f"  是否有效:    {'✅ 是' if remaining > 0 else '❌ 已过期'}" if remaining else "  是否有效:    ⚠️ 未知")


def cmd_search(keyword: str):
    """搜索公众号"""
    if not os.path.exists(_LOCAL_TOKEN_FILE):
        print("❌ 请先运行 login 命令登录")
        return

    gather = WxGather(tenant_id="local")
    if not gather.token:
        print("❌ Token 为空，请重新登录")
        return

    print(f"搜索: {keyword}")
    result = gather.search_biz(keyword, limit=10)

    if result is None:
        print("❌ 搜索失败，Token 可能已过期")
        return

    accounts = result.get("list", [])
    if not accounts:
        print("未找到相关公众号")
        return

    print(f"\n找到 {len(accounts)} 个公众号:\n")
    for i, biz in enumerate(accounts):
        print(f"  [{i}] {biz.get('nickname', '?')}")
        print(f"      fake_id: {biz.get('fakeid', '?')}")
        print(f"      简介: {(biz.get('signature', '') or '')[:80]}")
        print()


def cmd_collect(faker_id: str, mp_title: str = "", max_page: int = 1, gather_content: bool = True, interval: int = 10):
    """采集指定公众号的文章"""
    if not os.path.exists(_LOCAL_TOKEN_FILE):
        print("❌ 请先运行 login 命令登录")
        return

    print(f"\n开始采集: {mp_title or faker_id}")
    print(f"  fake_id: {faker_id}")
    print(f"  采集内容: {'是' if gather_content else '否（仅标题链接）'}")
    print(f"  最大页数: {max_page}")
    print()

    articles = []

    def on_article(art: dict) -> bool:
        articles.append(art)
        content_len = len(art.get("content", "") or "")
        print(f"  📄 {art.get('title', '?')[:60]}")
        print(f"     链接: {art.get('url', '?')[:80]}")
        print(f"     时间: {art.get('publish_time', '?')}")
        print(f"     正文: {content_len} 字符" if content_len else "")
        print()
        return True

    gather = MpsApi(tenant_id="local", gather_content=gather_content)

    try:
        gather.get_articles(
            faker_id=faker_id,
            mp_id=faker_id,
            mp_title=mp_title or faker_id,
            callback=on_article,
            max_page=max_page,
            interval=interval,
            gather_content=gather_content,
        )
    except Exception as e:
        print(f"\n❌ 采集出错: {e}")
        return

    print(f"\n✅ 完成: 共 {len(articles)} 篇文章")

    # 保存到本地 JSON 文件方便查看
    out_file = f"articles_{faker_id}.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(
            [{"title": a["title"], "url": a["url"], "publish_time": a.get("publish_time", ""),
              "content": (a.get("content", "") or "")[:500]} for a in articles],
            f, ensure_ascii=False, indent=2
        )
    print(f"结果已保存到: {out_file}")


def cmd_full(keyword: str, max_page: int = 1, gather_content: bool = True):
    """一键完整流程：登录 → 搜索 → 选择 → 采集"""
    # 1. 检查登录
    if not os.path.exists(_LOCAL_TOKEN_FILE):
        print("⚠️  尚未登录，开始扫码登录...")
        cmd_login()
        if not os.path.exists(_LOCAL_TOKEN_FILE):
            print("❌ 登录失败，退出")
            return

    # 2. 搜索
    print("\n" + "=" * 60)
    gather = WxGather(tenant_id="local")
    if not gather.token:
        print("❌ Token 无效，请重新 login")
        return

    print(f"搜索公众号: {keyword}")
    result = gather.search_biz(keyword, limit=10)

    if not result or not result.get("list"):
        print("❌ 未找到")
        return

    accounts = result["list"]
    print(f"\n搜索结果 ({len(accounts)}):")
    for i, biz in enumerate(accounts):
        nick = biz.get("nickname", "?")
        fakeid = biz.get("fakeid", "?")
        sig = (biz.get("signature", "") or "")[:60]
        print(f"  [{i}] {nick}  ({fakeid})")
        if sig:
            print(f"      {sig}")

    # 3. 让用户选择
    try:
        idx = int(input(f"\n选择序号 [0-{len(accounts)-1}]: "))
        if idx < 0 or idx >= len(accounts):
            print("无效序号")
            return
    except (ValueError, KeyboardInterrupt):
        return

    chosen = accounts[idx]
    faker_id = chosen["fakeid"]
    mp_title = chosen.get("nickname", faker_id)

    # 4. 采集
    cmd_collect(faker_id=faker_id, mp_title=mp_title, max_page=max_page, gather_content=gather_content)


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="WeChat MP 本地独立测试")
    sub = parser.add_subparsers(dest="cmd")

    # login
    sub.add_parser("login", help="扫码登录")

    # status
    sub.add_parser("status", help="查看登录状态")

    # search
    p_search = sub.add_parser("search", help="搜索公众号")
    p_search.add_argument("--kw", required=True, help="搜索关键词")

    # collect
    p_collect = sub.add_parser("collect", help="采集文章")
    p_collect.add_argument("--fakeid", required=True, help="公众号 fake_id")
    p_collect.add_argument("--title", default="", help="公众号名称（可选）")
    p_collect.add_argument("--max-page", type=int, default=1, help="最大页数（1页=5篇）")
    p_collect.add_argument("--content", action="store_true", default=True, help="采集正文")
    p_collect.add_argument("--no-content", dest="content", action="store_false", help="不采集正文")

    # full
    p_full = sub.add_parser("full", help="一键：搜索+采集")
    p_full.add_argument("--kw", required=True, help="搜索关键词")
    p_full.add_argument("--max-page", type=int, default=1, help="最大页数")
    p_full.add_argument("--content", action="store_true", default=True)
    p_full.add_argument("--no-content", dest="content", action="store_false")

    args = parser.parse_args()

    if args.cmd == "login":
        cmd_login()
    elif args.cmd == "status":
        cmd_status()
    elif args.cmd == "search":
        cmd_search(args.kw)
    elif args.cmd == "collect":
        cmd_collect(args.fakeid, args.title, args.max_page, args.content)
    elif args.cmd == "full":
        cmd_full(args.kw, args.max_page, args.content)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
