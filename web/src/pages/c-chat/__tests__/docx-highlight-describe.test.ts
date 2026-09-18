// describePlaceholderSpan 纯函数单测：docx 保真预览槽位的文本/悬浮说明。
// 四象限（有/无中文名 × 已填/未填）+ 脏值边界。样式副作用留在
// stylePlaceholderSpan（DOM 脚手架在本仓库已腐坏，不做渲染测试）。
import { describe, expect, it } from 'vitest';

import { describePlaceholderSpan } from '../docx-highlight';

describe('describePlaceholderSpan', () => {
  it('已填 + 有中文名：正文显示值，title 为「中文名（key）」', () => {
    expect(
      describePlaceholderSpan('XX项目', '项目名称', 'project_name'),
    ).toEqual({
      text: 'XX项目',
      title: '项目名称（project_name）',
      phName: '项目名称',
    });
  });

  it('已填 + 无中文名：正文显示值，title 回落 key（旧行为不变）', () => {
    expect(describePlaceholderSpan('v1', undefined, 'k1')).toEqual({
      text: 'v1',
      title: 'k1',
      phName: undefined,
    });
  });

  it('未填 + 有中文名：正文显示中文名（不再暴露英文 key）', () => {
    expect(describePlaceholderSpan('', '项目名称', 'project_name')).toEqual({
      text: '项目名称',
      title: '项目名称（等待 AI 填入）',
      phName: '项目名称',
    });
  });

  it('未填 + 无中文名：正文与 title 均回落 key', () => {
    expect(describePlaceholderSpan(undefined, undefined, 'k1')).toEqual({
      text: 'k1',
      title: 'k1（等待 AI 填入）',
      phName: undefined,
    });
  });

  it('空串 / undefined 均判为未填（与渲染层 `v !== ""` 口径一致）', () => {
    expect(describePlaceholderSpan('', '甲', 'k1').title).toBe(
      '甲（等待 AI 填入）',
    );
    expect(describePlaceholderSpan(undefined, '甲', 'k1').title).toBe(
      '甲（等待 AI 填入）',
    );
  });

  it('空白串算未填（对齐后端 derive_unfilled 的 strip 口径，非「不做 trim」）', () => {
    expect(describePlaceholderSpan('  ', '甲', 'k1')).toEqual({
      text: '甲',
      title: '甲（等待 AI 填入）',
      phName: '甲',
    });
  });

  it('null 算未填（与 buildFilledRows 把 null 归一为空串同口径）', () => {
    expect(
      describePlaceholderSpan(null as unknown as string, '甲', 'k1'),
    ).toEqual({ text: '甲', title: '甲（等待 AI 填入）', phName: '甲' });
  });

  it('"0" / "false" 算已填（与后端一致：字符串 "0" 非留白）', () => {
    expect(describePlaceholderSpan('0', '甲', 'k1').text).toBe('0');
    expect(describePlaceholderSpan('false', '甲', 'k1').text).toBe('false');
  });

  it('name 为空串 → 回落 key（`name || key`，不产生空标题）', () => {
    expect(describePlaceholderSpan('v', '', 'k1').title).toBe('k1');
    expect(describePlaceholderSpan(undefined, '', 'k1').text).toBe('k1');
  });

  it('值含中文名同名文本不被误处理（原样透传）', () => {
    expect(
      describePlaceholderSpan(
        '项目名称（project_name）',
        '项目名称',
        'project_name',
      ).text,
    ).toBe('项目名称（project_name）');
  });
});
