import { ChevronDown } from 'lucide-react';

import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible';
import { type TplPlaceholder } from '@/hooks/use-template-fill-request';
import { CELL_STATUS } from './status';

// 任务详情抽屉与测试填写对话框共用的逐格结果表：行=填写点、值 narrow、
// 单元格状态徽标、证据展开。数据结构安全 narrow（Task 9 同款策略）。

interface FillResultTableProps {
  placeholders: TplPlaceholder[];
  /** values.render 产值 map（xlsx/docx 通用 unknown，内部 narrow） */
  values: unknown;
  /** values.cells 逐格状态 map */
  cells: unknown;
  /** evidence map：key → { query, chunks } */
  evidence: unknown;
}

function asRecord(v: unknown): Record<string, unknown> | null {
  return v && typeof v === 'object' && !Array.isArray(v)
    ? (v as Record<string, unknown>)
    : null;
}

// Task 9 类型放宽为 unknown，这里做安全 narrow：字符串原样，对象 JSON 化，其余 String()
function formatValue(v: unknown): string {
  if (v == null) return '';
  if (typeof v === 'string') return v;
  if (typeof v === 'object') {
    try {
      return JSON.stringify(v);
    } catch {
      return String(v);
    }
  }
  return String(v);
}

// 证据 chunk 可能是 JSON 字符串（doc_name/similarity/content）也可能是纯文本
function parseChunk(chunk: unknown): {
  docName: string;
  similarity: string;
  content: string;
} {
  let raw: unknown = chunk;
  if (typeof chunk === 'string') {
    try {
      raw = JSON.parse(chunk);
    } catch {
      return { docName: '', similarity: '', content: chunk.slice(0, 120) };
    }
  }
  const obj = asRecord(raw);
  if (obj) {
    return {
      docName: typeof obj.doc_name === 'string' ? obj.doc_name : '',
      similarity: obj.similarity != null ? String(obj.similarity) : '',
      content: typeof obj.content === 'string' ? obj.content.slice(0, 120) : '',
    };
  }
  return { docName: '', similarity: '', content: String(raw).slice(0, 120) };
}

function EvidenceSection({ chunks }: { chunks: unknown }) {
  const list = Array.isArray(chunks) ? chunks.slice(0, 3) : [];
  if (list.length === 0) {
    return <p className="text-xs text-muted-foreground">无检索证据</p>;
  }
  return (
    <Collapsible>
      <CollapsibleTrigger className="flex items-center gap-1 text-xs text-blue-600 hover:underline">
        检索证据（{list.length}）
        <ChevronDown className="size-3" />
      </CollapsibleTrigger>
      <CollapsibleContent>
        <div className="mt-1 space-y-2 rounded bg-muted/40 p-2">
          {list.map((chunk, i) => {
            const { docName, similarity, content } = parseChunk(chunk);
            return (
              <div key={i} className="text-xs">
                <div className="flex items-center gap-2 text-muted-foreground">
                  {docName && <span className="font-medium">{docName}</span>}
                  {similarity && <span>相似度 {similarity}</span>}
                </div>
                <p className="mt-0.5 leading-5">{content}</p>
              </div>
            );
          })}
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
}

// 逐格一行：占位符名 + 值 + 状态 + 证据展开
function CellRow({
  placeholder,
  renderValue,
  cellStatus,
  chunks,
}: {
  placeholder: TplPlaceholder | undefined;
  renderValue: unknown;
  cellStatus: string;
  chunks: unknown;
}) {
  const statusMeta = CELL_STATUS[cellStatus];
  const label = statusMeta?.label ?? '未知';
  const cls = statusMeta?.cls ?? '';
  const key = placeholder?.key ?? '';
  return (
    <div className="border-b py-2.5 last:border-b-0">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-sm font-medium">
            {placeholder?.name || key || '（未知填写点）'}
          </p>
          {key && (
            <p className="font-mono text-xs text-muted-foreground">{key}</p>
          )}
        </div>
        <span className={`shrink-0 rounded px-2 py-0.5 text-xs ${cls}`}>
          {label}
        </span>
      </div>
      <p className="mt-1 whitespace-pre-wrap break-words text-sm">
        {renderValue == null || renderValue === '' ? (
          <span className="text-muted-foreground">-</span>
        ) : (
          formatValue(renderValue)
        )}
      </p>
      <div className="mt-1.5">
        <EvidenceSection chunks={chunks} />
      </div>
    </div>
  );
}

export function FillResultTable({
  placeholders,
  values,
  cells,
  evidence,
}: FillResultTableProps) {
  const renderMap = asRecord(values);
  const cellsMap = asRecord(cells);
  const evidenceMap = asRecord(evidence);

  if (placeholders.length === 0) {
    return (
      <p className="rounded border py-8 text-center text-sm text-muted-foreground">
        范本暂无填写点配置
      </p>
    );
  }
  return (
    <div>
      {placeholders.map((ph) => {
        const cell = cellsMap?.[ph.key];
        const cellStatus = typeof cell === 'string' ? cell : '未知';
        const ev = asRecord(evidenceMap?.[ph.key]);
        return (
          <CellRow
            key={ph.key}
            placeholder={ph}
            renderValue={renderMap?.[ph.key]}
            cellStatus={cellStatus}
            chunks={ev?.chunks}
          />
        );
      })}
    </div>
  );
}
