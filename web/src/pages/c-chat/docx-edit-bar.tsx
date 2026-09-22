// web/src/pages/c-chat/docx-edit-bar.tsx
// 保真编辑工具条：portal 进父级吸顶容器（toolbarHost）。改动数/结构提示/错误
// + 保存/放弃。与 DocxToolbar（Lexical 格式按钮）无关——保真编辑 v1 只产文本
// edits/tableEdits，无 run 级格式操作。
import { Button } from '@/components/ui/button';

export default function DocxEditBar({
  dirty,
  saving,
  error,
  hint,
  onSave,
  onDiscard,
}: {
  dirty: number;
  saving: boolean;
  error: string;
  hint: string;
  onSave: () => void;
  onDiscard: () => void;
}) {
  return (
    <div className="flex items-center gap-2 rounded-b-md border border-t-0 border-[#E5E5E5] bg-white px-3 py-1.5 shadow-[0_2px_8px_rgba(0,0,0,0.06)]">
      <span className="text-xs text-[#8A8A8A]">
        保真编辑 · 仅支持段内文字修改
      </span>
      {dirty > 0 && (
        <span className="text-xs font-medium text-[#FA8C16]">
          改动 {dirty} 处
        </span>
      )}
      {hint && !error && <span className="text-xs text-[#FAAD14]">{hint}</span>}
      {error && (
        <span className="truncate text-xs text-[#FF4D4F]">{error}</span>
      )}
      <div className="ml-auto flex items-center gap-2">
        <Button
          size="sm"
          variant="outline"
          onClick={onDiscard}
          disabled={saving}
        >
          放弃
        </Button>
        <Button size="sm" onClick={onSave} disabled={saving || dirty === 0}>
          {saving ? '保存中…' : '保存为新版本'}
        </Button>
      </div>
    </div>
  );
}
