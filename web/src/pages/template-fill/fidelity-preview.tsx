// B端范本详情「保真预览」：拉原始文件（kind=original）经 docx-preview 保真渲染
// （字号/加粗/颜色/表格/排版与源文档一致），已注册填写点按锚文本琥珀色高亮
// （复用 c-chat/docx-highlight 的 highlightDocxRanges 归一化匹配）。
// 渲染/拉取失败经 onRenderFailed 通知父组件自动降级文本模式。
// 注意与 C端 useTemplateFillFile（render 工作副本、含 {{key}}）的区别：
// B端预览看源文档原貌，填写点是留白/锚文本而非 {{key}}。文案全中文。
import {
  applyDocxPageLazy,
  highlightDocxRanges,
} from '@/pages/c-chat/docx-highlight';
import api from '@/utils/api';
import request from '@/utils/request';
import { renderAsync } from 'docx-preview';
import { useEffect, useRef, useState } from 'react';

/** 定位闪烁时长（与 C端确认卡定位一致） */
const FLASH_MS = 2000;

/** 滚动居中到某填写点 mark 并琥珀色闪烁提示；无 mark 返回 false */
function focusAnchor(container: HTMLElement, key: string): boolean {
  const el = container.querySelector<HTMLElement>(
    `mark[data-anchor-key="${CSS.escape(key)}"]`,
  );
  if (!el) return false;
  el.scrollIntoView({ block: 'center', behavior: 'smooth' });
  const prev = el.style.outline;
  el.style.outline = '2px solid #f59e0b';
  window.setTimeout(() => {
    el.style.outline = prev;
  }, FLASH_MS);
  return true;
}

export default function FidelityPreview({
  templateId,
  anchors,
  onRenderFailed,
  focusKey,
  onMarked,
}: {
  templateId: string;
  /** 已注册填写点锚文本高亮项（key 唯一；anchor 空串的行跳过；addr 用于同形留白顺序分配） */
  anchors: Array<{ key: string; anchor: string; addr?: string }>;
  /** 渲染失败回调（父组件降级文本模式） */
  onRenderFailed: () => void;
  /** 需定位的填写点 key（列表行点击触发） */
  focusKey?: string | null;
  /** 渲染高亮完成后回传成功标记的 key 集合（未命中的行由父组件标「未定位」） */
  onMarked?: (marked: Set<string>) => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  // 回调经 ref 透传：renderDoc 为普通函数不感知 props 新鲜度
  const anchorsRef = useRef(anchors);
  anchorsRef.current = anchors;
  const failedRef = useRef(false);
  const onMarkedRef = useRef(onMarked);
  onMarkedRef.current = onMarked;
  const onRenderFailedRef = useRef(onRenderFailed);
  onRenderFailedRef.current = onRenderFailed;
  // 原件 blob 缓存：anchors 首帧后到齐（接口竞速）或变化时用同一 blob 重渲染，
  // 避免高亮跑在空 anchors 上导致 onMarked 空集合（全部「未定位」的根因）
  const blobRef = useRef<Blob | null>(null);
  // 渲染序号：拉取渲染与 anchors 触发的重渲染竞争时只保留最后一次
  const renderSeqRef = useRef(0);
  // 渲染完成门控：定位必须等高亮跑完
  const [renderedOk, setRenderedOk] = useState(false);
  // 一次性防重：同一 (templateId, key) 只定位一次；定位失败不标记，可重试
  const focusDoneRef = useRef<string | null>(null);

  // anchors 的稳定签名：父组件每次 render 重建数组（filter/map），不能直接做 effect 依赖
  const anchorsSig = anchors
    .map((a) => `${a.key}\u0000${a.anchor}\u0000${a.addr ?? ''}`)
    .join('\u0001');

  const renderDoc = () => {
    const blob = blobRef.current;
    const el = containerRef.current;
    if (!blob || !el) return;
    const seq = ++renderSeqRef.current;
    setRenderedOk(false);
    focusDoneRef.current = null;
    el.innerHTML = '';
    renderAsync(blob, el, undefined, {
      inWrapper: true,
      breakPages: true,
    })
      .then(() => {
        if (seq !== renderSeqRef.current) return;
        // 屏外分页懒渲染（大文档滚动优化，与 C端同款）
        applyDocxPageLazy(el);
        // 已注册填写点按锚文本高亮：去空白归一化匹配，同 key 只标一处；
        // 同形留白多项按 addr 文档序分配第 1/2/…次出现（occ 语义）；
        // 页眉/页脚/文本框未渲染时不标（静默）。
        // showKeyBadge：高亮处追加 {{key}} 徽标，直观看填写点对应占位符
        // 过滤用原文长度而非 trim 后长度：纯空格留白 anchor（raw 通道）trim 后为空，
        // 误过滤会导致这类行全部「未定位」；通道判定交给 highlightDocxRanges 内部
        const marked = highlightDocxRanges(
          el,
          anchorsRef.current
            .filter((a) => a.anchor && a.anchor.length >= 2)
            .map((a) => ({
              text: a.anchor,
              key: a.key,
              color: '#f59e0b',
              addr: a.addr,
            })),
          { showKeyBadge: true },
        );
        setRenderedOk(true);
        onMarkedRef.current?.(marked);
      })
      .catch((e) => {
        if (seq !== renderSeqRef.current) return;
        console.warn('[FidelityPreview] render failed:', e);
        if (!failedRef.current) {
          failedRef.current = true;
          onRenderFailedRef.current();
        }
      });
  };

  // 拉原件 → 首次渲染。anchors 不进依赖（ref 透传），anchors 到齐由下方签名 effect 触发重渲染
  useEffect(() => {
    if (!templateId || !containerRef.current) return;
    let cancelled = false;
    failedRef.current = false;
    blobRef.current = null;
    setRenderedOk(false);
    focusDoneRef.current = null;
    containerRef.current.innerHTML = '';

    request
      .get(api.downloadTemplateFill(templateId, 'original'), {
        responseType: 'blob',
      })
      .then((res) => {
        const blob = res.data as Blob;
        if (cancelled) return;
        if (!blob || blob.size === 0) throw new Error('empty blob');
        // 后端出错时返回的是 JSON（code != 0），blob 里装的是错误信息而非文件
        if (blob.type.includes('application/json')) {
          return blob.text().then((t) => {
            let msg = '原件加载失败';
            try {
              msg = JSON.parse(t).message || msg;
            } catch {
              // 保留默认文案
            }
            throw new Error(msg);
          });
        }
        return blob;
      })
      .then((blob) => {
        if (cancelled || !blob) return;
        blobRef.current = blob;
        renderDoc();
      })
      .catch((e) => {
        // 线上排查降级原因（网络/JSON 错误体/docx 解析失败）；
        // 用户提示由父组件统一给（降级文本模式 + 一次性 warning）
        console.warn('[FidelityPreview] fetch failed:', e);
        if (!cancelled && !failedRef.current) {
          failedRef.current = true;
          onRenderFailedRef.current();
        }
      });

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [templateId]);

  // anchors 实际变化（首次到齐 / 识别或保存后）→ 缓存 blob 重渲染 + 重跑高亮
  useEffect(() => {
    if (blobRef.current) renderDoc();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [anchorsSig]);

  // 列表行点击定位：等渲染完成才尝试；同一目标只定位一次；无 mark 静默
  useEffect(() => {
    const root = containerRef.current;
    if (!templateId || !focusKey || !renderedOk || !root) return;
    const target = `${templateId}:${focusKey}`;
    if (focusDoneRef.current === target) return;
    if (focusAnchor(root, focusKey)) focusDoneRef.current = target;
  }, [focusKey, renderedOk, templateId]);

  return (
    <div className="max-h-[65vh] overflow-auto">
      {/* 说明条：高亮语义 + 划选指引 */}
      <div className="mb-2 rounded bg-amber-50 px-3 py-1.5 text-xs text-amber-700">
        按 Word 原始格式渲染；琥珀色下划线为已注册填写点（按锚文本匹配），
        并标注对应占位符 {'{{key}}'}。划选标记新填写点请切换「文本模式」。
      </div>
      <div ref={containerRef} />
    </div>
  );
}
