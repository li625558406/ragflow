import { ArrowLeft } from 'lucide-react';
import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import message from '@/components/ui/message';
import {
  useDetectTemplateFill,
  useSaveTemplateFillPlaceholders,
  useTemplateFillDetail,
  useTemplateFillPreview,
  type TplPlaceholder,
} from '@/hooks/use-template-fill-request';
import api from '@/utils/api';
import { CreateTaskDialog } from './create-task-dialog';
import {
  collectRowErrors,
  emptyPlaceholder,
  PlaceholderTable,
  trimRows,
} from './placeholder-table';
import { TestFillDialog } from './test-fill-dialog';

const STATUS_LABEL: Record<string, string> = {
  draft: '草稿',
  published: '已发布',
  disabled: '已停用',
};

const STATUS_BADGE_CLS: Record<string, string> = {
  draft: 'bg-gray-200 text-gray-700',
  published: 'bg-green-100 text-green-700',
  disabled: 'bg-red-100 text-red-600',
};

// 高亮 {{key}} 占位符：preview 接口只标第一个，前端把 text 中所有 {{xxx}} 都替换为徽标
const PLACEHOLDER_RE = /\{\{([a-z][a-z0-9_]*)\}\}/g;

function renderTextWithPlaceholders(text: string) {
  const nodes: React.ReactNode[] = [];
  let last = 0;
  let match: RegExpExecArray | null;
  PLACEHOLDER_RE.lastIndex = 0;
  while ((match = PLACEHOLDER_RE.exec(text)) !== null) {
    if (match.index > last) {
      nodes.push(text.slice(last, match.index));
    }
    nodes.push(
      <span
        key={match.index}
        className="mx-0.5 rounded bg-yellow-600 px-1 font-mono text-xs text-white"
      >
        {`{{${match[1]}}}`}
      </span>,
    );
    last = match.index + match[0].length;
  }
  if (last < text.length) {
    nodes.push(text.slice(last));
  }
  return nodes;
}

export default function TemplateFillDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();

  const { data: detailRes, isLoading: detailLoading } = useTemplateFillDetail(
    id ?? '',
  );
  const {
    data: previewRes,
    isLoading: previewLoading,
    error: previewError,
  } = useTemplateFillPreview(id ?? '');

  const detail = detailRes?.data;
  const previewItems = previewRes?.data?.items ?? [];

  const [placeholders, setPlaceholders] = useState<TplPlaceholder[]>([]);
  const [rowErrors, setRowErrors] = useState<Record<string, boolean>>({});
  const [createOpen, setCreateOpen] = useState(false);
  const [testOpen, setTestOpen] = useState(false);

  const saveMut = useSaveTemplateFillPlaceholders();
  const detectMut = useDetectTemplateFill();

  // detect 建议与现有手动行合并（与上传向导同规则）：
  // 建议在前、手动行（无定位 addr）在后，key 重复的手动行丢弃，避免覆盖手动添加的行
  const applySuggestions = (suggestions: TplPlaceholder[]) => {
    if (suggestions.length === 0) {
      message.warning('未识别到填写点，可手动添加');
      return;
    }
    setPlaceholders((prev) => {
      const sugKeys = new Set(suggestions.map((s) => s.key.trim()));
      const manual = prev.filter((r) => !r.addr && !sugKeys.has(r.key.trim()));
      return [...suggestions, ...manual];
    });
    message.success(`AI 识别到 ${suggestions.length} 个填写点，请确认后保存`);
  };

  const runDetect = () => {
    if (!id) return;
    detectMut.mutate(id, {
      onSuccess: (res) => applySuggestions(res.suggestions),
      onError: (err) =>
        message.error(
          err instanceof Error ? err.message : 'AI 识别失败，可手动添加填写点',
        ),
    });
  };

  // 详情首次加载（或切换模板）时用后端占位符初始化编辑表。
  // 依赖含 placeholders：内容不变时 React Query structural sharing 保持引用稳定，
  // 编辑不会被刷新覆盖；保存后 refetch 返回新引用时会以服务端数据（含回填 addr）对齐。
  useEffect(() => {
    if (detail?.placeholders) {
      setPlaceholders(detail.placeholders);
      setRowErrors({});
    }
  }, [detail?.id, detail?.placeholders]);

  // 已停用模板右侧编辑区整体只读
  const readonly = detail?.status === 'disabled';

  const updateRow = (index: number, patch: Partial<TplPlaceholder>) => {
    setPlaceholders((prev) =>
      prev.map((row, i) => (i === index ? { ...row, ...patch } : row)),
    );
    setRowErrors((prev) => {
      const next = { ...prev };
      Object.keys(next).forEach((k) => {
        if (k.startsWith(`${index}-`)) delete next[k];
      });
      return next;
    });
  };

  const removeRow = (index: number) => {
    setPlaceholders((prev) => prev.filter((_, i) => i !== index));
    setRowErrors({});
  };

  const saveConfig = () => {
    if (!id) return;
    const rows = trimRows(placeholders);
    setPlaceholders(rows);
    const errors = collectRowErrors(rows);
    setRowErrors(errors);
    if (Object.keys(errors).length > 0) {
      message.error('存在格式不正确的填写点，请检查标红字段');
      return;
    }
    saveMut.mutate(
      { id, placeholders: rows },
      {
        onSuccess: () => {
          message.success('保存成功');
          // hooks 内部已统一 invalidate 列表/详情/预览，此处无需重复
        },
        onError: (err) => {
          message.error(err instanceof Error ? err.message : '保存失败');
        },
      },
    );
  };

  const openDownload = (kind: 'original' | 'render') => {
    if (!id) return;
    window.open(api.downloadTemplateFill(id, kind));
  };

  // xlsx 预览按 sheet 分组（保持 items 中 sheet 首次出现的顺序）
  const sheetGroups: { sheet: string; items: typeof previewItems }[] = [];
  if (detail?.file_type === 'xlsx') {
    const bySheet = new Map<string, typeof previewItems>();
    previewItems.forEach((item) => {
      const sheet = item.sheet || '';
      if (!bySheet.has(sheet)) {
        bySheet.set(sheet, []);
        sheetGroups.push({ sheet, items: bySheet.get(sheet)! });
      }
      bySheet.get(sheet)!.push(item);
    });
  }

  if (detailLoading) {
    return (
      <div className="flex items-center justify-center py-16">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-b-transparent" />
      </div>
    );
  }

  if (!detail) {
    return (
      <div className="flex flex-col items-center justify-center py-16 text-muted-foreground">
        <p>模板不存在或已被删除</p>
        <Button
          variant="outline"
          size="sm"
          className="mt-4"
          onClick={() => navigate('/template-fill')}
        >
          返回列表
        </Button>
      </div>
    );
  }

  const renderReady = detail.render_ready;

  return (
    <div className="flex flex-col gap-4">
      {/* 顶部模板信息条 */}
      <Card className="bg-transparent border-none">
        <CardHeader>
          <div className="flex flex-wrap items-center gap-3">
            <Button
              size="icon-sm"
              variant="ghost"
              onClick={() => navigate('/template-fill')}
              title="返回列表"
            >
              <ArrowLeft className="size-[1.1em]" />
            </Button>
            <CardTitle className="text-2xl">{detail.name}</CardTitle>
            <span className="rounded px-2 py-0.5 text-xs">
              {detail.file_type === 'docx' ? 'Word' : 'Excel'}
            </span>
            <span
              className={`rounded px-2 py-0.5 text-xs ${
                STATUS_BADGE_CLS[detail.status] ?? STATUS_BADGE_CLS.draft
              }`}
            >
              {STATUS_LABEL[detail.status] ?? detail.status}
            </span>
            <span className="text-sm text-muted-foreground">
              版本 v{detail.latest_version}
            </span>
            {detail.status === 'published' && (
              <div className="ml-auto flex items-center gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => setTestOpen(true)}
                >
                  测试填写
                </Button>
                <Button size="sm" onClick={() => setCreateOpen(true)}>
                  发起填写
                </Button>
              </div>
            )}
          </div>
          {detail.description && (
            <p className="mt-1 text-sm text-muted-foreground">
              {detail.description}
            </p>
          )}
        </CardHeader>
      </Card>

      {/* 左预览 / 右配置 两栏，窄屏上下堆叠 */}
      <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-2">
        {/* 左侧：模板预览 */}
        <Card>
          <CardHeader>
            <CardTitle className="text-lg">模板预览</CardTitle>
          </CardHeader>
          <CardContent>
            {previewLoading ? (
              <div className="flex items-center justify-center py-12">
                <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-b-transparent" />
              </div>
            ) : previewError ? (
              <p className="text-sm text-red-500">
                {previewError instanceof Error
                  ? previewError.message
                  : '预览加载失败'}
              </p>
            ) : previewItems.length === 0 ? (
              <p className="py-8 text-center text-sm text-muted-foreground">
                暂无预览内容
              </p>
            ) : detail.file_type === 'xlsx' ? (
              <div className="max-h-[65vh] overflow-auto">
                {sheetGroups.map(({ sheet, items }) => (
                  <div key={sheet} className="mb-4 last:mb-0">
                    <div className="mb-2 border-b pb-1 text-sm font-medium">
                      {sheet || '工作表'}
                    </div>
                    {items.map((item) => (
                      <div
                        key={item.index}
                        className={`border-b px-2 py-1.5 text-sm last:border-b-0 ${
                          item.placeholder_key ? 'bg-yellow-100' : ''
                        }`}
                      >
                        {item.coord && (
                          <span className="mr-2 font-mono text-xs text-muted-foreground">
                            {item.coord}
                          </span>
                        )}
                        {renderTextWithPlaceholders(item.text)}
                      </div>
                    ))}
                  </div>
                ))}
              </div>
            ) : (
              <div className="max-h-[65vh] space-y-1 overflow-auto">
                {previewItems.map((item) => (
                  <p
                    key={item.index}
                    className={`px-2 py-1 text-sm leading-6 ${
                      item.placeholder_key ? 'bg-yellow-100' : ''
                    }`}
                  >
                    {renderTextWithPlaceholders(item.text)}
                  </p>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        {/* 右侧：填写点配置 */}
        <Card>
          <CardHeader>
            <CardTitle className="text-lg">填写点配置</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            {readonly && (
              <div className="rounded border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-700">
                模板已停用，配置只读
              </div>
            )}
            <div className="max-h-[55vh] overflow-auto">
              {placeholders.length === 0 ? (
                <p className="py-8 text-center text-sm text-muted-foreground">
                  暂无填写点，点击下方「添加填写点」
                </p>
              ) : (
                <PlaceholderTable
                  rows={placeholders}
                  errors={rowErrors}
                  onUpdate={updateRow}
                  onRemove={removeRow}
                  disabled={readonly}
                />
              )}
            </div>
            <div className="flex items-center gap-3">
              <Button
                size="sm"
                variant="outline"
                disabled={readonly || detectMut.isPending}
                onClick={runDetect}
              >
                {detectMut.isPending ? '识别中…' : 'AI 识别'}
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={readonly}
                onClick={() =>
                  setPlaceholders((prev) => [...prev, emptyPlaceholder()])
                }
              >
                添加填写点
              </Button>
              <Button
                size="sm"
                disabled={readonly || saveMut.isPending}
                onClick={saveConfig}
              >
                {saveMut.isPending ? '保存中…' : '保存配置'}
              </Button>
              <span className="text-xs text-muted-foreground">
                共 {placeholders.length} 个填写点
              </span>
            </div>
            <div className="flex gap-2 border-t pt-3">
              <Button
                size="sm"
                variant="outline"
                onClick={() => openDownload('original')}
              >
                下载原件
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={!renderReady}
                title={renderReady ? '下载工作副本' : '请先保存填写点配置'}
                onClick={() => openDownload('render')}
              >
                下载工作副本
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>
      <CreateTaskDialog
        templateId={id ?? ''}
        open={createOpen}
        onOpenChange={setCreateOpen}
      />
      <TestFillDialog
        templateId={id ?? ''}
        open={testOpen}
        onOpenChange={setTestOpen}
      />
    </div>
  );
}
