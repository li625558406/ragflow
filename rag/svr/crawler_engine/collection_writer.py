"""
CollectionWriter — 智能采集（新系统）的数据库写入器。

与 BidWriter 完全独立：
- 写入目标：crawler_result（共用主表）+ collection_policy_ext / collection_personnel_ext / collection_objection_ext（按类别）
- 不读 bid_* 任何表，不复用 BidProjectService
- id 生成策略：md5(site_id|source_url)，与 CrawlerResultService.gen_result_id 一致

调用方式：
    writer = CollectionWriter(kb_id="xxx", tenant_id="yyy")
    writer.write_all(item_dict, site_id="ggcy_zcwj", category="policy")

每条 item 写入错误不影响其他 item（错误隔离）。
"""
import hashlib
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

try:
    from common.time_utils import current_timestamp
except ImportError:
    # Fall back to local epoch ms when running outside the app (e.g. unit tests)
    import time
    def current_timestamp() -> int:
        return int(time.time() * 1000)


# Lazy imports — only available at runtime inside Docker container
try:
    from api.db.services.crawler_service import CrawlerResultService
    from api.db.services.collection_ext_service import (
        CollectionPolicyExtService,
        CollectionPersonnelExtService,
        CollectionObjectionExtService,
    )
except ImportError:
    CrawlerResultService = None  # type: ignore
    CollectionPolicyExtService = None  # type: ignore
    CollectionPersonnelExtService = None  # type: ignore
    CollectionObjectionExtService = None  # type: ignore


# 合法的 category 值
VALID_CATEGORIES = ("bid", "policy", "personnel", "news", "other", "objection")


def gen_result_id(site_id: str, source_url: str) -> str:
    """与 crawler_service.gen_result_id 一致：md5(site_id|source_url)。"""
    return hashlib.md5(f"{site_id}|{source_url}".encode("utf-8")).hexdigest()


# 异议结果字段：中文标签 → 英文字段名（顺序敏感：长标签须在短标签之前，避免被部分匹配抢占）
_OBJECTION_FIELD_LABELS: List[tuple] = [
    ("异议处理意见", "handling_opinion"),
    ("被异议人名称", "objected_party_name"),
    ("公示时间", "publication_time"),
    ("招标代理机构", "tender_agency"),
    ("招标编号", "tender_no"),
    ("相关标段(包)", "related_sections"),
    ("业主单位", "owner_unit"),
    ("异议人名称", "objector_name"),
    ("异议时间", "objection_time"),
    ("异议类型", "objection_type"),
    ("异议内容", "objection_content"),
    ("依据和理由", "basis_and_reasons"),
    ("受理时间", "acceptance_time"),
    ("处理时间", "processing_time"),
    ("处理结果", "processing_result"),
    ("处理依据", "processing_basis"),
    ("编号", "record_no"),
]
_OBJECTION_FIELD_KEYS: List[str] = [en for _, en in _OBJECTION_FIELD_LABELS]


def _parse_objection_fields(content: str) -> Dict[str, str]:
    """从异议详情正文中按 'Label：value' 模式抽取结构化字段。

    支持中英文冒号 + 任意空白；字段值延伸到下一个 'xxx：' 标签或段落结尾。
    多行值（如异议内容）会被压缩为单行空格分隔，便于 CharField 存储。

    关键：用否定逆向断言 `(?<![\\u4e00-\\u9fff])` 防止短标签匹配到
    长标签的子串（如 `编号` 不能匹配 `招标编号` 内部的 `编号`）。
    用 `(?=\\n\\s*[^：：\\n]{1,20}[：:])` 限定下一标签长度 ≤20 字符，
    避免跨过无冒号的章节标题（如孤立的 `异议` 行）。
    """
    if not content:
        return {}
    fields: Dict[str, str] = {}
    # 注意：不能用 str.format()，因为正则中的 {1,20} 会被当成格式占位符
    # lookbehind 防子串抢占（编号 不匹配 招标编号 内部的 编号）
    # lookahead 用已知标签列表交替，能识别同一行内紧挨的下一个标签
    # （如 "异议内容：详见异议函 依据和理由：" — 两个标签在同一行无换行分隔）
    _labels_alt = "|".join(re.escape(lbl) for lbl, _ in _OBJECTION_FIELD_LABELS)
    pattern_suffix = (
        r"\s*[：:]\s*"
        r"(.+?)"
        rf"(?=\s*(?:{_labels_alt})\s*[：:]|\Z)"
    )
    # 已知章节标题（无冒号的孤立行），会在懒匹配中被吸入值末尾，需 strip
    _CHAPTER_TRAILERS = ("异议处理", "异议")
    for cn_label, en_key in _OBJECTION_FIELD_LABELS:
        pattern = r"(?<![\u4e00-\u9fff])" + re.escape(cn_label) + pattern_suffix
        m = re.search(pattern, content, re.DOTALL)
        if not m:
            continue
        val = m.group(1).strip()
        # 压缩多余空白（含换行），保留可读性
        val = re.sub(r"\s+", " ", val).strip()
        # 去除末尾被吸入的章节标题（如 "福建广誉...公司 异议"）
        for trailer in _CHAPTER_TRAILERS:
            if val == trailer:
                val = ""
                break
            if val.endswith(" " + trailer):
                val = val[: -(len(trailer) + 1)].strip()
        if val:
            fields[en_key] = val
    return fields


class CollectionWriter:
    """智能采集结果写入器。

    根据 category 决定写入策略：
      - 共用：crawler_result（所有类别）
      - policy:    crawler_result + collection_policy_ext
      - personnel: crawler_result + collection_personnel_ext
      - objection: crawler_result + collection_objection_ext
      - bid/news/other: 仅 crawler_result（结构化字段放 extracted_json）
    """

    def __init__(self, kb_id: str = "", tenant_id: str = "", date_filter: str = ""):
        self._kb_id = kb_id
        self._tenant_id = tenant_id
        # date_filter: "" 不过滤；"today" 只保当天；"YYYY-MM-DD" 只保指定日期
        self._date_filter = self._normalize_date_filter(date_filter)
        self._stats = {
            "results_new": 0,
            "results_updated": 0,
            "results_failed": 0,
            "results_filtered_out": 0,
            "policy_ext_written": 0,
            "policy_ext_failed": 0,
            "personnel_ext_written": 0,
            "personnel_ext_failed": 0,
            "objection_ext_written": 0,
            "objection_ext_failed": 0,
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def write_all(
        self,
        item: Dict[str, Any],
        site_id: str,
        category: str = "bid",
        task_id: str = "",
        url: Optional[str] = None,
        site_display: str = "",
    ) -> Optional[str]:
        """写入一条采集结果到所有目标表。

        Args:
            item: 归一化后的采集项字典（含 title/url/date/content 等）
            site_id: YAML 站点 id（内部 key，用于 result_id 生成/状态跟踪）
            category: bid|policy|personnel|news|other|objection
            task_id: 关联的 CrawlerTask.id（可空）
            url: 覆盖 item.url，用于 ID 生成
            site_display: 展示用站点串（"中文名称 域名"），由调用方从 YAML 拼接传入

        Returns:
            result_id 写入成功；None 写入失败或被日期过滤掉。
        """
        if category not in VALID_CATEGORIES:
            logging.warning("CollectionWriter: invalid category=%s, fallback to 'bid'", category)
            category = "bid"

        # 0. 日期过滤（可选）— 不匹配则直接跳过，不写库不报警
        if self._date_filter and not self._item_matches_date(item):
            self._stats["results_filtered_out"] += 1
            return None

        # 1. 生成 result_id
        source_url = url or item.get("url") or item.get("href") or ""
        if not source_url:
            logging.warning(
                "CollectionWriter: cannot resolve URL for item: %s",
                str(item.get("title", ""))[:80],
            )
            self._stats["results_failed"] += 1
            return None

        result_id = gen_result_id(site_id, source_url)

        # 2. 写 crawler_result（共用主表）
        if not self._write_result(result_id, item, site_id, category, task_id, source_url, site_display):
            return None

        # 3. 按 category 路由扩展表
        if category == "policy":
            self._write_policy_ext(result_id, item)
        elif category == "personnel":
            self._write_personnel_ext(result_id, item)
        elif category == "objection":
            self._write_objection_ext(result_id, item)

        return result_id

    # ------------------------------------------------------------------
    # Internal: date filter
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_date_filter(val: str) -> str:
        """规整 date_filter 为 YYYY-MM-DD 或空字符串。"""
        if not val:
            return ""
        val = val.strip().lower()
        if val == "today":
            from datetime import date
            return date.today().isoformat()
        # 接受 YYYY-MM-DD / YYYY/MM/DD
        try:
            from datetime import datetime as _dt
            return _dt.strptime(val.replace("/", "-"), "%Y-%m-%d").date().isoformat()
        except ValueError:
            logging.warning("CollectionWriter: invalid date_filter=%r, ignored", val)
            return ""

    def _item_matches_date(self, item: Dict[str, Any]) -> bool:
        """判断 item 的日期是否匹配 self._date_filter。

        尝试多种常见日期字段（date/publishDate/pubDate/date_str 等），
        解析失败或无日期字段视为不匹配（严格模式）。
        """
        from datetime import datetime as _dt
        candidate_keys = (
            "date", "publishDate", "publishTime", "pubDate", "pub_date",
            "publish_date", "date_str", "effective_date",
        )
        for k in candidate_keys:
            v = item.get(k)
            if not v:
                continue
            try:
                # 兼容 "2024-07-22"、"2024/07/22"、"2024-07-22 10:30:00" 等
                text = str(v).strip()[:10].replace("/", "-")
                parsed = _dt.strptime(text, "%Y-%m-%d").date().isoformat()
                return parsed == self._date_filter
            except (ValueError, TypeError):
                continue
        # 无可用日期字段 → 不匹配
        return False

    # ------------------------------------------------------------------
    # Internal: crawler_result 主表写入
    # ------------------------------------------------------------------

    def _write_result(
        self,
        result_id: str,
        item: Dict[str, Any],
        site_id: str,
        category: str,
        task_id: str,
        source_url: str,
        site_display: str = "",
    ) -> bool:
        if CrawlerResultService is None:
            logging.warning("CollectionWriter: CrawlerResultService not available, skipping")
            return False

        try:
            # 拼装 markdown 正文
            markdown = self._build_markdown(item)

            # 抽取附件
            attachments = self._extract_attachments(item)

            # 结构化字段原样保留到 extracted_json（供前端/Agent 读取）
            extracted_json = {
                k: v for k, v in item.items()
                if k not in ("content", "content_html", "detail_html", "html",
                             "detail", "text", "body", "description",
                             "files", "fileList", "attachments", "appendixList")
                and v not in (None, "", [], {})
            }
            extracted_json["_category"] = category

            data = {
                "id": result_id,
                "task_id": task_id,
                "tenant_id": self._tenant_id,
                "site_id": site_id,
                "site_display": site_display,
                "title": (item.get("title") or item.get("name") or "Untitled")[:1024],
                "source_url": source_url,
                "publish_date": self._normalize_date(item),
                "markdown": markdown,
                "extracted_json": extracted_json,
                "attachments": attachments,
                "status": "raw",
                "kb_doc_id": "",
                "error_msg": "",
                "crawled_at": current_timestamp(),
            }

            is_new = CrawlerResultService.upsert_result(data)
            if is_new:
                self._stats["results_new"] += 1
            else:
                self._stats["results_updated"] += 1
            return True
        except Exception as e:
            logging.error("CollectionWriter: result write failed (id=%s): %s", result_id, e)
            self._stats["results_failed"] += 1
            return False

    # ------------------------------------------------------------------
    # Internal: 扩展表写入
    # ------------------------------------------------------------------

    def _write_policy_ext(self, result_id: str, item: Dict[str, Any]) -> bool:
        if CollectionPolicyExtService is None:
            return False

        # 字段映射：YAML extract.fields 映射的目标 key（标准名）+ 原始字段兜底
        fields = {
            "doc_number": self._first_of(item, "doc_number", "wenhao", "documentNumber",
                                         "document_number", "fwh"),
            "issuing_authority": self._first_of(item, "issuing_authority", "fawenjigou",
                                                "issuer", "publishOrg"),
            "authority_level": self._first_of(item, "authority_level", "xiaoli",
                                              "effectLevel", "level"),
            "topic_category": self._first_of(item, "topic_category", "category",
                                             "topic", "zhuti"),
            "effective_date": self._parse_date(self._first_of(
                item, "effective_date", "implementation_date", "effectiveDate")),
            "expiry_date": self._parse_date(self._first_of(
                item, "expiry_date", "invalid_date", "expiryDate")),
            "status": self._first_of(item, "status", "policy_status") or "有效",
            "legal_basis": self._first_of(item, "legal_basis", "yiju", "basis"),
        }

        try:
            CollectionPolicyExtService.upsert(result_id, fields)
            self._stats["policy_ext_written"] += 1
            return True
        except Exception as e:
            logging.error("CollectionWriter: policy_ext failed (id=%s): %s", result_id, e)
            self._stats["policy_ext_failed"] += 1
            return False

    def _write_personnel_ext(self, result_id: str, item: Dict[str, Any]) -> bool:
        if CollectionPersonnelExtService is None:
            return False

        fields = {
            "person_name": self._first_of(item, "person_name", "name", "xm",
                                          "personName", "realName"),
            "id_card_masked": self._mask_id_card(self._first_of(
                item, "id_card", "id_card_no", "idCardNo", "sfzh")),
            "cert_no": self._first_of(item, "cert_no", "zsh", "certificateNo",
                                      "certNo", "zsbh"),
            "cert_type": self._first_of(item, "cert_type", "zslx", "certificateType",
                                        "certType", "zy"),
            "employer": self._first_of(item, "employer", "unit", "danwei",
                                       "employer_name", "qymc"),
            "specialty": self._first_of(item, "specialty", "zhuanye", "specialty_name",
                                        "zymc"),
            "position": self._first_of(item, "position", "zw", "title"),
            "valid_until": self._parse_date(self._first_of(
                item, "valid_until", "valid_date", "yxq", "validUntil")),
            "status": self._first_of(item, "status", "personnel_status") or "注册",
        }

        try:
            CollectionPersonnelExtService.upsert(result_id, fields)
            self._stats["personnel_ext_written"] += 1
            return True
        except Exception as e:
            logging.error("CollectionWriter: personnel_ext failed (id=%s): %s", result_id, e)
            self._stats["personnel_ext_failed"] += 1
            return False

    def _write_objection_ext(self, result_id: str, item: Dict[str, Any]) -> bool:
        if CollectionObjectionExtService is None:
            return False

        # 从详情正文按中文标签解析 17 个字段
        content = (item.get("content") or item.get("detail") or
                   item.get("text") or item.get("body") or "")
        parsed = _parse_objection_fields(content) if content else {}

        # 兜底：若 item 已预先携带英文字段（YAML extract 映射过），优先取 item 值
        fields = {}
        for en_key in _OBJECTION_FIELD_KEYS:
            val = self._first_of(item, en_key)
            if val in (None, "", [], {}):
                val = parsed.get(en_key, "")
            fields[en_key] = val

        try:
            CollectionObjectionExtService.upsert(result_id, fields)
            self._stats["objection_ext_written"] += 1
            return True
        except Exception as e:
            logging.error("CollectionWriter: objection_ext failed (id=%s): %s", result_id, e)
            self._stats["objection_ext_failed"] += 1
            return False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _first_of(item: Dict[str, Any], *keys: str) -> Any:
        """按优先级返回第一个非空值。"""
        for k in keys:
            v = item.get(k)
            if v not in (None, "", [], {}):
                return v
        return ""

    @staticmethod
    def _normalize_date(item: Dict[str, Any]) -> str:
        """归一化发布日期为字符串。"""
        for k in ("date", "publishDate", "publishTime", "pubDate",
                  "pub_date", "publish_date"):
            v = item.get(k)
            if v:
                return str(v).strip()[:64]
        return ""

    @staticmethod
    def _parse_date(val: Any) -> Optional[datetime]:
        """尝试解析日期；失败返回 None。"""
        if not val:
            return None
        if isinstance(val, datetime):
            return val
        try:
            from .content_converter import parse_date
            return parse_date(val)
        except Exception:
            return None

    @staticmethod
    def _mask_id_card(id_card: Any) -> str:
        """身份证号脱敏：保留前 6 后 4，中间用 *。"""
        if not id_card:
            return ""
        s = str(id_card).strip()
        if len(s) <= 10:
            return s[:2] + "***" + s[-2:] if len(s) > 4 else s
        return s[:6] + "*" * 8 + s[-4:]

    @staticmethod
    def _build_markdown(item: Dict[str, Any]) -> str:
        """把采集项格式化为 markdown，供 KB 上传使用。"""
        lines = []
        title = item.get("title") or item.get("name") or "Untitled"
        lines.append(f"# {title}")
        lines.append("")

        date_val = CollectionWriter._normalize_date(item)
        if date_val:
            lines.append(f"**日期:** {date_val}")
            lines.append("")

        url = item.get("url") or item.get("href") or ""
        if url:
            lines.append(f"**来源:** {url}")
            lines.append("")

        source_site = item.get("source_site") or item.get("site_id") or ""
        if source_site:
            lines.append(f"**站点:** {source_site}")
            lines.append("")

        # 正文（清洗后）
        content = (item.get("content") or item.get("detail") or
                   item.get("text") or item.get("body") or "")
        if content:
            try:
                from .content_converter import clean_content
                cleaned = clean_content(str(content))
            except Exception:
                cleaned = str(content)
            if cleaned:
                lines.append(cleaned)
                lines.append("")

        # 附件链接
        attachments = CollectionWriter._extract_attachments(item)
        if attachments:
            lines.append("---")
            lines.append("")
            lines.append("**附件**")
            lines.append("")
            for att in attachments:
                name = att.get("file_name") or "attachment"
                link = att.get("file_url") or ""
                if link:
                    lines.append(f"- [{name}]({link})")
            lines.append("")

        return "\n".join(lines)

    @staticmethod
    def _extract_attachments(item: Dict[str, Any]) -> List[Dict[str, Any]]:
        """从 item 提取附件列表，归一化为 [{file_name, file_url, ...}]。"""
        results: List[Dict[str, Any]] = []
        for key in ("attachments", "files", "fileList", "appendixList"):
            val = item.get(key)
            if isinstance(val, list):
                for f in val:
                    if isinstance(f, dict):
                        url = (f.get("file_url") or f.get("fileUrl") or
                               f.get("url") or f.get("downloadUrl") or "")
                        if not url:
                            continue
                        results.append({
                            "file_name": (f.get("file_name") or f.get("fileName") or
                                          f.get("name") or "attachment")[:500],
                            "file_url": url[:1000],
                            "file_suffix": (f.get("file_suffix") or f.get("fileSuffix") or
                                            CollectionWriter._guess_suffix(url))[:20],
                            "file_size": int(f.get("file_size") or f.get("fileSize") or 0),
                        })
        return results

    @staticmethod
    def _guess_suffix(url: str) -> str:
        if not url:
            return ""
        from urllib.parse import urlparse
        path = urlparse(url).path
        parts = path.rsplit(".", 1)
        if len(parts) == 2 and len(parts[1]) <= 8:
            return "." + parts[1].lower()
        return ""

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    @property
    def stats(self) -> Dict[str, int]:
        return dict(self._stats)

    def reset_stats(self) -> None:
        for k in self._stats:
            self._stats[k] = 0
