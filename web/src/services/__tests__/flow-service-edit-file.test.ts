// 对话附件编辑服务函数：URL/方法/snake_case 请求体/file_name 注入/错误传播。
// apiFetch 走全局 fetch + localStorage，这里直接打桩两者，不发真实请求。
import {
  editFileDocument,
  editFlowDocument,
  type FlowDocEditOps,
} from '@/services/flow-service';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const fetchMock = vi.fn();

beforeEach(() => {
  vi.resetModules();
  fetchMock.mockReset();
  vi.stubGlobal('fetch', fetchMock);
  vi.stubGlobal('localStorage', {
    getItem: vi.fn((k: string) =>
      k === 'Authorization' ? 'tok' : k === 'userInfo' ? '{"id":"u1"}' : null,
    ),
    removeItem: vi.fn(),
  });
});

const ops: FlowDocEditOps = {
  edits: [
    {
      paraIndex: 2,
      newText: '改后',
      runs: [{ text: '改后', bold: true }],
      align: 'center',
      headingLevel: 1,
    },
  ],
  deletes: [3],
  inserts: [{ afterParaIndex: -1, newText: '文首' }],
  tableEdits: [{ paraIndex: 4, row: 1, col: 0, newText: '单元格' }],
};

const okResp = (data: unknown) =>
  Promise.resolve({
    ok: true,
    status: 200,
    json: () => Promise.resolve({ code: 0, data }),
  } as Response);

describe('editFileDocument（对话附件编辑端点契约）', () => {
  it('POST /files/<id>/edit：camelCase ops 转 snake_case 并携带 file_name', async () => {
    fetchMock.mockReturnValue(
      okResp({ file_id: 'newid', file_name: '文档_编辑.docx' }),
    );
    const res = await editFileDocument('fid123', '文档.docx', ops);
    expect(res).toEqual({ file_id: 'newid', file_name: '文档_编辑.docx' });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain('/api/v1/files/fid123/edit?');
    expect(url).toContain('user_id=u1');
    expect(init.method).toBe('POST');
    const body = JSON.parse(init.body);
    expect(body.file_name).toBe('文档.docx');
    expect(body.edits[0]).toMatchObject({
      para_index: 2,
      new_text: '改后',
      runs: [{ text: '改后', bold: true }],
      align: 'center',
      heading_level: 1,
    });
    expect(body.deletes).toEqual([3]);
    expect(body.inserts[0]).toMatchObject({
      after_para_index: -1,
      new_text: '文首',
    });
    expect(body.table_edits[0]).toMatchObject({
      para_index: 4,
      row: 1,
      col: 0,
      new_text: '单元格',
    });
  });

  it('code!=0 时抛后端 message（面向用户的可读错误）', async () => {
    fetchMock.mockReturnValue(
      Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ code: 102, message: '非法的文件 id' }),
      } as Response),
    );
    await expect(editFileDocument('bad', 'x.docx', ops)).rejects.toThrow(
      '非法的文件 id',
    );
  });

  it('文件名缺省时兜底 document.docx', async () => {
    fetchMock.mockReturnValue(okResp({ file_id: 'n', file_name: 'n' }));
    await editFileDocument('fid', '', ops);
    expect(JSON.parse(fetchMock.mock.calls[0][1].body).file_name).toBe(
      'document.docx',
    );
  });
});

describe('editFlowDocument（流程版本编辑，共用 ops 映射）', () => {
  it('仍带 version_id 且 ops 转 snake_case（重构回归闸）', async () => {
    fetchMock.mockReturnValue(okResp({ version: { id: 'v2' } }));
    await editFlowDocument('flow1', 'v1', ops);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain('/api/v1/flow/flow1/document/edit?');
    const body = JSON.parse(init.body);
    expect(body.version_id).toBe('v1');
    expect(body.edits[0].para_index).toBe(2);
    expect(body.edits[0].heading_level).toBe(1);
  });
});
