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
