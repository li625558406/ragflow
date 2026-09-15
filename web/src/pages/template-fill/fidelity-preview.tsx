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
import { useEffect, useRef } from 'react';

export default function FidelityPreview({
  templateId,
  anchors,
  onRenderFailed,
}: {
  templateId: string;
  /** 已注册填写点锚文本高亮项（key 唯一；anchor 空串的行跳过；addr 用于同形留白顺序分配） */
  anchors: Array<{ key: string; anchor: string; addr?: string }>;
  /** 渲染失败回调（父组件降级文本模式） */
  onRenderFailed: () => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  // anchors 变化不重渲染整文档：只重跑高亮，需最新值进 done 回调
  const anchorsRef = useRef(anchors);
  anchorsRef.current = anchors;
  const failedRef = useRef(false);

  useEffect(() => {
    if (!templateId || !containerRef.current) return;
    const el = containerRef.current;
    let cancelled = false;
    failedRef.current = false;
    el.innerHTML = '';

    const fail = () => {
      if (cancelled || failedRef.current) return;
      failedRef.current = true;
      onRenderFailed();
    };

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
        return renderAsync(blob, el, undefined, {
          inWrapper: true,
          breakPages: true,
        }).then(() => {
          if (cancelled) return;
          // 屏外分页懒渲染（大文档滚动优化，与 C端同款）
          applyDocxPageLazy(el);
          // 已注册填写点按锚文本高亮：去空白归一化匹配，同 key 只标一处；
          // 同形留白多项按 addr 文档序分配第 1/2/…次出现（occ 语义）；
          // 页眉/页脚/文本框未渲染时不标（静默）。
          // showKeyBadge：高亮处追加 {{key}} 徽标，直观看填写点对应占位符
          highlightDocxRanges(
            el,
            anchorsRef.current
              .filter((a) => a.anchor && a.anchor.trim().length >= 2)
              .map((a) => ({
                text: a.anchor,
                key: a.key,
                color: '#f59e0b',
                addr: a.addr,
              })),
            { showKeyBadge: true },
          );
        });
      })
      .catch((e) => {
        // 线上排查降级原因（网络/JSON 错误体/docx 解析失败）；
        // 用户提示由父组件统一给（降级文本模式 + 一次性 warning）
        console.warn('[FidelityPreview] render failed:', e);
        fail();
      });

    return () => {
      cancelled = true;
    };
    // anchors 经 ref 透传，避免高亮项变化触发整文档重渲染
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [templateId]);

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
