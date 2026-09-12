import { collectRecentDownloads } from './recent-downloads';

const dl = (doc_id: string) => ({
  doc_id,
  filename: `${doc_id}.docx`,
  mime_type: 'x',
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
});
