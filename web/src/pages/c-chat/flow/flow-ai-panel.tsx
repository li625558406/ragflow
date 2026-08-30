// web/src/pages/c-chat/flow/flow-ai-panel.tsx
// AI 处理面板：复用 c-chat 对话智能体（SSE 流式），支持附带当前版本文件、
// 回复可「仅存记录」或「存为新版本」（复用 flow-service.saveFlowAiRecord）。
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { useSendMessageBySSE } from '@/hooks/use-send-message';
import { downloadVersionBlob, saveFlowAiRecord } from '@/services/flow-service';
import api from '@/utils/api';
import { Bot, Send, Square } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import type { FlowAiChatItem, FlowVersionItem } from './flow-types';

const NO_AGENT_HINT = '未配置对话智能体，请先在「对话」页签使用过智能体对话';

export default function FlowAiPanel({
  flowId,
  version,
  aiChats,
  onSaved,
}: {
  flowId: string;
  version: FlowVersionItem | null;
  aiChats: FlowAiChatItem[];
  onSaved: () => void;
}) {
  const [instruction, setInstruction] = useState('');
  const [attachFile, setAttachFile] = useState(true);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  // agent_id 与 c-chat 同源：localStorage（c-chat 发送时写入）
  const [agentId] = useState(
    () => localStorage.getItem('ragflow_agent_id') || '',
  );
  const instructionRef = useRef('');
  const sessionIdRef = useRef('');

  const {
    send,
    streamState,
    done,
    stopOutputMessage,
    resetAnswerList,
    answerList,
  } = useSendMessageBySSE(api.agentChatCompletion, {
    excludeFanOutFromContent: false,
  });

  // 从流式事件中提取 session_id（多轮续聊依赖）
  useEffect(() => {
    const sid = answerList.find((e: any) => e?.session_id)?.session_id;
    if (sid) sessionIdRef.current = sid;
  }, [answerList]);

  const busy = !done;
  const responseText = streamState.content.trim();
  const hasContent = responseText.length > 0;
  // 后端按 create_time 正序返回，取最后 3 条即最近记录
  const recentChats = aiChats.slice(-3);

  const handleSend = useCallback(async () => {
    const query = instruction.trim();
    if (!query || busy) return;
    if (!agentId) {
      setError(NO_AGENT_HINT);
      return;
    }
    setError('');
    // 保存记录时要用（发送后输入框即清空）
    instructionRef.current = query;
    setInstruction('');

    // 附带当前版本文件：下载 blob → File → /documents/upload → 文档对象数组
    let files: unknown[] = [];
    if (attachFile && version) {
      try {
        const blob = await downloadVersionBlob(flowId, version.id);
        const file = new File([blob], version.file_name, {
          type: version.file_type || 'application/octet-stream',
        });
        const fd = new FormData();
        fd.append('file', file);
        const resp = await fetch('/api/v1/documents/upload', {
          method: 'POST',
          headers: {
            Authorization: localStorage.getItem('Authorization') || '',
          },
          body: fd,
        });
        const result = await resp.json();
        if (result.code === 0 && result.data) {
          files = Array.isArray(result.data) ? result.data : [result.data];
        }
      } catch {
        // 附件上传失败不阻断发送，降级为无文件提问
        files = [];
      }
    }

    const res = await send({
      agent_id: agentId,
      query,
      session_id: sessionIdRef.current,
      stream: true,
      files,
      internet: false,
    });

    if (res && (res.response.status !== 200 || (res.data as any)?.code !== 0)) {
      setError(
        (res.data as any)?.message || `请求失败（HTTP ${res.response.status}）`,
      );
    }
  }, [agentId, attachFile, busy, flowId, instruction, send, version]);

  const handleSave = useCallback(
    async (asVersion: boolean) => {
      if (!hasContent || saving) return;
      setSaving(true);
      setError('');
      try {
        await saveFlowAiRecord(flowId, {
          instruction: instructionRef.current || '(见记录)',
          response: responseText,
          version_id: version?.id,
          session_id: sessionIdRef.current,
          save_as_version: asVersion,
        });
        resetAnswerList();
        onSaved();
      } catch (e: any) {
        setError(e?.message || '保存失败');
      } finally {
        setSaving(false);
      }
    },
    [
      flowId,
      hasContent,
      onSaved,
      resetAnswerList,
      responseText,
      saving,
      version?.id,
    ],
  );

  return (
    <div className="shrink-0 border-t border-[#F0F0F0] px-4 py-3">
      {/* 标题行 */}
      <div className="flex items-center gap-2">
        <Bot className="h-4 w-4 shrink-0 text-[#1668DC]" />
        <span className="shrink-0 text-sm font-medium">AI 处理</span>
        <span className="min-w-0 flex-1 truncate text-xs text-[#999]">
          （上下文：
          {version
            ? `v${version.version_no} ${version.file_name}`
            : '无上下文文件'}
          ）
        </span>
        <label className="flex shrink-0 cursor-pointer items-center gap-1 text-xs text-[#666]">
          <input
            type="checkbox"
            className="h-3.5 w-3.5 accent-[#1668DC]"
            checked={attachFile}
            disabled={!version}
            onChange={(e) => setAttachFile(e.target.checked)}
          />
          附带当前版本文件
        </label>
      </div>

      {/* 历史记录摘要（最近 3 条） */}
      {recentChats.length > 0 && (
        <div className="mt-2 max-h-20 space-y-1 overflow-y-auto rounded bg-[#FAFAFA] px-2 py-1.5">
          {recentChats.map((c) => (
            <div key={c.id} className="truncate text-xs text-[#999]">
              指令：{c.instruction} →{' '}
              {c.output_version_id ? '已存为新版本' : '未存版本'}
            </div>
          ))}
        </div>
      )}

      {!agentId && (
        <div className="mt-2 text-xs text-[#FAAD14]">{NO_AGENT_HINT}</div>
      )}
      {error && <div className="mt-2 text-xs text-red-500">{error}</div>}

      {/* 流式输出区 */}
      {(busy || hasContent) && (
        <pre className="mt-2 max-h-40 overflow-y-auto whitespace-pre-wrap rounded bg-[#F7F8FA] px-2 py-1.5 text-xs leading-relaxed text-[#333]">
          {streamState.content}
          {busy && <span className="animate-pulse">▌</span>}
        </pre>
      )}

      {/* 输入行 */}
      <div className="mt-2 flex items-end gap-2">
        <Textarea
          value={instruction}
          onChange={(e) => setInstruction(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              handleSend();
            }
          }}
          placeholder="输入 AI 处理指令，例如：提取第三章关键条款并总结"
          rows={1}
          disabled={busy}
          className="min-h-9 flex-1 resize-none text-sm"
        />
        {busy ? (
          <Button size="sm" variant="outline" onClick={stopOutputMessage}>
            <Square className="mr-1 h-3 w-3" />
            停止
          </Button>
        ) : (
          <Button
            size="sm"
            disabled={!instruction.trim() || !agentId}
            onClick={handleSend}
          >
            <Send className="mr-1 h-3.5 w-3.5" />
            发送
          </Button>
        )}
        {done && hasContent && (
          <>
            <Button
              size="sm"
              variant="outline"
              disabled={saving}
              onClick={() => handleSave(false)}
            >
              仅存记录
            </Button>
            <Button
              size="sm"
              disabled={saving}
              onClick={() => handleSave(true)}
            >
              存为新版本
            </Button>
          </>
        )}
      </div>
    </div>
  );
}
