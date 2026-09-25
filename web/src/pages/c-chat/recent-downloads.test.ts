import type { ITemplateFillState } from '@/hooks/template-fill-stream';
import {
  collectRecentDownloads,
  collectRecentFillDownloads,
} from './recent-downloads';

const dl = (doc_id: string) => ({
  doc_id,
  filename: `${doc_id}.docx`,
  mime_type: 'x',
});

const state = (templates: any[]): ITemplateFillState =>
  ({ templates }) as ITemplateFillState;

const filledTpl = (template_id: string, doc_id: string, extra: any = {}) => ({
  template_id,
  name: template_id,
  status: 'filled',
  download: { doc_id, filename: `${doc_id}.docx`, mime_type: 'x' },
  ...extra,
});

describe('collectRecentDownloads', () => {
  it('取最近2张，新卡在前', () => {
    const msgs = [
      { downloads: [dl('a1')] },
      {},
      { downloads: [dl('b1'), dl('b2')] },
      { downloads: [dl('c1')] },
    ];
    expect(collectRecentDownloads(msgs).map((d) => d.doc_id)).toEqual([
      'c1',
      'b2',
    ]);
  });

  it('空/无downloads安全', () => {
    expect(collectRecentDownloads([])).toEqual([]);
    expect(collectRecentDownloads([{}, { downloads: [] }])).toEqual([]);
  });

  it('doc_id 非字符串或缺失跳过', () => {
    expect(
      collectRecentDownloads([
        {
          downloads: [
            { doc_id: 1 as any, filename: 'x' },
            { filename: 'y' } as any,
            dl('ok'),
          ],
        },
      ]),
    ).toEqual([{ doc_id: 'ok', filename: 'ok.docx' }]);
  });

  it('filename 缺失回退 name，再缺失空串', () => {
    expect(
      collectRecentDownloads([
        { downloads: [{ doc_id: 'x', name: '名字' } as any] },
      ]),
    ).toEqual([{ doc_id: 'x', filename: '名字' }]);
  });

  it('对抗：超长 filename 截断 120，控制字符剥除', () => {
    const longName = '很'.repeat(300);
    const dirty = `成\u0000稿\u001f标书\n第2节\tv${'\u007f'}1.docx`;
    expect(
      collectRecentDownloads([
        { downloads: [{ doc_id: 'long', filename: longName }] },
      ]),
    ).toEqual([{ doc_id: 'long', filename: '很'.repeat(120) }]);
    expect(
      collectRecentDownloads([
        { downloads: [{ doc_id: 'dirty', filename: dirty }] },
      ]),
    ).toEqual([{ doc_id: 'dirty', filename: '成稿标书第2节v1.docx' }]);
    // name 回退路径同样清洗
    expect(
      collectRecentDownloads([
        {
          downloads: [
            { doc_id: 'n', name: `a\r\u001bb${'c'.repeat(200)}` } as any,
          ],
        },
      ]),
    ).toEqual([{ doc_id: 'n', filename: `ab${'c'.repeat(118)}` }]);
  });
});

describe('collectRecentFillDownloads', () => {
  it('取 filled 且带 doc_id 的模板行，倒序（后填在前），limit 2', () => {
    const s = state([
      filledTpl('t1', 'tplfill-a'),
      filledTpl('t2', 'tplfill-b'),
      filledTpl('t3', 'tplfill-c'),
    ]);
    expect(collectRecentFillDownloads(s).map((d) => d.doc_id)).toEqual([
      'tplfill-c',
      'tplfill-b',
    ]);
  });

  it('非 filled 行（filling/failed/selected）跳过', () => {
    const s = state([
      { template_id: 't1', name: 'x', status: 'filling' },
      { template_id: 't2', name: 'y', status: 'failed', error: 'e' },
      { template_id: 't3', name: 'z', status: 'selected' },
      filledTpl('t4', 'tplfill-ok'),
    ]);
    expect(collectRecentFillDownloads(s)).toEqual([
      { doc_id: 'tplfill-ok', filename: 'tplfill-ok.docx' },
    ]);
  });

  it('filled 但 download 缺失/doc_id 非字符串跳过', () => {
    const s = state([
      { template_id: 't1', name: 'x', status: 'filled' },
      {
        template_id: 't2',
        name: 'y',
        status: 'filled',
        download: { doc_id: 1 as any, filename: 'n' },
      },
      filledTpl('t3', 'tplfill-ok'),
    ]);
    expect(collectRecentFillDownloads(s).map((d) => d.doc_id)).toEqual([
      'tplfill-ok',
    ]);
  });

  it('空态/undefined/空 templates 安全返回 []（调用方回落流程版本）', () => {
    expect(collectRecentFillDownloads(undefined)).toEqual([]);
    expect(collectRecentFillDownloads(state([]))).toEqual([]);
    expect(collectRecentFillDownloads({} as ITemplateFillState)).toEqual([]);
  });

  it('对抗：filename 控制字符剥除 + 超长截断 120', () => {
    const dirty = `成\u0000稿\u001f标书\n第2节\tv${'\u007f'}1.docx`;
    const s = state([filledTpl('t1', 'tplfill-dirty')]);
    s.templates[0].download!.filename = dirty;
    expect(collectRecentFillDownloads(s)).toEqual([
      { doc_id: 'tplfill-dirty', filename: '成稿标书第2节v1.docx' },
    ]);
    const long = state([filledTpl('t1', 'tplfill-long')]);
    long.templates[0].download!.filename = '很'.repeat(300);
    expect(collectRecentFillDownloads(long)[0].filename).toBe('很'.repeat(120));
  });

  it('filename 缺失回退 download.name', () => {
    const s = state([filledTpl('t1', 'tplfill-n')]);
    s.templates[0].download = {
      doc_id: 'tplfill-n',
      name: '名字',
      filename: '',
      mime_type: 'x',
    } as any;
    expect(collectRecentFillDownloads(s)).toEqual([
      { doc_id: 'tplfill-n', filename: '名字' },
    ]);
  });
});
