# 范本库 PDF 上传适配设计（转 docx 后 AI 识别）

日期：2026-09-14 ｜ 状态：已确认（引擎裁定 LibreOffice）

## 1. 问题与约束

范本库（B端 template-fill）目前仅收 .docx / .doc / .xlsx。用户需要直接上传 PDF 范本，先转 docx，再走既有 AI 识别。

第一性原理：模板填写全链路（`docx_utils` 编址、`extract_docx_candidates` 候选、docxtpl 渲染、标蓝、预览、下载）只认 docx/xlsx 两种形态。与其给每条链路增加 PDF 解析分支，不如在**唯一入口** `upload_template` 做一次格式归一化——旧版 .doc 已验证该模式（上传即转 docx，原始格式不留存，全链路按 docx）。

转换引擎已裁定 **LibreOffice**（复用容器内 soffice，与 .doc 转换同链路，零新增依赖）。

## 2. 改动面

### 2.1 后端 `api/apps/restful_apis/template_api.py`

- 新增 `_is_pdf(filename)`：镜像 `_is_legacy_doc`（endswith(".pdf")，大小写不敏感，None 安全）。
- `_convert_doc_to_docx(blob)` 泛化为 `_convert_to_docx(blob, src_ext=".doc")`：
  - src 临时文件名 `input{src_ext}`（soffice 按后缀选 import filter），输出恒为 `input.docx`；
  - soffice 命令、独立 UserInstallation、LD_LIBRARY_PATH、timeout 全部不变；
  - .doc 调用点行为逐字节不变（默认参数）。
- `upload_template`：
  - `.pdf` 分支：`is_pdf=True`、`file_type="docx"`、`blob = await asyncio.to_thread(_convert_to_docx, blob, ".pdf")`（线程池包装防阻塞事件循环；注意：.doc 分支的同款 to_thread 包装属他人未提交 hunk，本 commit 中 .doc 仍为同步调用，其阻塞风险随该 hunk 落地消除）；
  - 转换失败文案：「PDF 转换失败，建议用 Word/WPS 打开后另存为 .docx 再上传」；
  - 转换后体积复查复用现有逻辑；zip 容器校验复用（转换产物必须是合法 docx）；
  - 提示文案更新：「请上传 .docx / .doc / .pdf / .xlsx 模板文件」「仅支持 .docx / .doc / .pdf / .xlsx」。
- `_ZERO_CANDIDATES_MSG` 追加 PDF 子句：扫描件/排版型 PDF 转换后无留白特征是 0 候选的真实常见原因，提示「若为 PDF 转换而来，格式可能丢失，建议用 Word/WPS 另存为 .docx 后重新上传」。

### 2.2 前端 `web/src/pages/template-fill/upload-wizard.tsx`

4 处：
- `TEMPLATE_FILE_RE = /\.(docx|doc|pdf|xlsx)$/i`（:27）
- 校验错误文案「仅支持 .docx / .doc / .pdf / .xlsx」（:133）
- `accept=".docx,.doc,.pdf,.xlsx"`（:197）+ label（:193）
- 拖拽区提示文案（:219）

串行上传逻辑（防 soffice 转换挤占容器资源）不变——PDF 与 .doc 同走 soffice，天然被该逻辑覆盖。

### 2.3 测试 `test/test_template_api_routes.py`

- `_is_pdf` 纯函数边界：大小写、`.pdfx` 不误伤、无扩展名、None。
- `_convert_to_docx` 后缀传参：monkeypatch `subprocess.run` 捕获 src 路径——默认参数仍为 `input.doc`（存量不变）、显式 `".pdf"` 时为 `input.pdf`。
- 上传 happy path：monkeypatch `_convert_to_docx` 返回合法 docx bytes，断言入库 `file_type="docx"`；失败路径返回转换失败文案。
- 更新既有 monkeypatch 点适配新签名（`lambda blob, src_ext=".doc": ...`）。

## 3. 已知边界与风险

- soffice 的 PDF 导入是 Draw 系导入，转出 docx 的文字多位于**文本框/框架**内。识别链已天然兼容：本次加固 `_build_addr_map` 已支持 `:tx<k>:` 文本框编址；替换走 docxtpl（文本框内容位于 document.xml 的 w:txbxContent，{{key}} 可正常渲染）；标蓝已覆盖文本框。但版式保真度不保证，需真实样张验证后评估。
- 图片型扫描 PDF 转出空 docx → 0 候选 → `_ZERO_CANDIDATES_MSG` 兜底文案。
- 原始 PDF 不留存（与 .doc 策略一致）；C 端无需新增 PDF 预览（存储即为 docx）。
- 20MB 上传限制与转换后体积复查沿用。

## 4. 验收

1. 新增/更新测试全绿（8 套件回归，唯一已知无关失败 test_convert_doc_to_docx_success 为工作区他人 timeout 改动所致）。
2. 前端 `npm run build` 通过。
3. 部署后真实 PDF 样张人工验证：上传 → 识别出填写点 → 填写渲染正常。

## 5. 部署清单（未部署，由用户指示）

后端：`api/apps/restful_apis/template_api.py`；前端：`upload-wizard.tsx`（build + dist SCP）。无数据库变更。
