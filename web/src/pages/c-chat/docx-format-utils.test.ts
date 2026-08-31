import {
  mergeStyle,
  parseStyle,
  runsFmtSig,
  stripBgFromStyle,
} from './docx-format-utils';

describe('parseStyle', () => {
  it('空串/undefined → 空对象', () => {
    expect(parseStyle('')).toEqual({});
    expect(parseStyle(undefined as any)).toEqual({});
  });
  it('解析 font-size/color/background-color', () => {
    expect(
      parseStyle('font-size: 14pt;color:#FF0000;background-color: #FFF100;'),
    ).toEqual({
      'font-size': '14pt',
      color: '#FF0000',
      'background-color': '#FFF100',
    });
  });
  it('容忍多余分号与非法片段', () => {
    expect(parseStyle('color:red;; ;;font-weight: bold')).toEqual({
      color: 'red',
      'font-weight': 'bold',
    });
  });
});

describe('mergeStyle', () => {
  it('设置新键、覆盖已有键、删除键', () => {
    expect(
      mergeStyle('color:#111;font-size:12pt', { color: '#222' }, ['font-size']),
    ).toBe('color:#222');
  });
  it('patch 与 remove 同键时以 remove 为准', () => {
    expect(mergeStyle('color:#111', { color: '#222' }, ['color'])).toBe('');
  });
});

describe('stripBgFromStyle', () => {
  it('剔除 background-color，其余保留（批注高亮 UI 色不落盘）', () => {
    expect(stripBgFromStyle('background-color:#FFF1B8;color:#333')).toBe(
      'color:#333',
    );
  });
});

describe('runsFmtSig', () => {
  it('相同样式序列 → 相同签名；文本不同不影响签名', () => {
    const a = [{ text: '甲', bold: true, color: '#FF0000' }, { text: '乙' }];
    const b = [{ text: '丙', bold: true, color: '#FF0000' }, { text: '丁' }];
    expect(runsFmtSig(a)).toBe(runsFmtSig(b));
  });
  it('样式不同 → 签名不同', () => {
    const a = [{ text: 'x', bold: true }];
    const b = [{ text: 'x', italic: true }];
    expect(runsFmtSig(a)).not.toBe(runsFmtSig(b));
  });
  it('undefined → undefined', () => {
    expect(runsFmtSig(undefined)).toBeUndefined();
  });
});
