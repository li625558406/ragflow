/**
 * 填写点行校验（trimRows/collectRowErrors）：
 * 核心回归——纯空白锚文本是「留白位」的合法形态（run 层识别产物），
 * trim 不得毁掉它、校验不得标红它（2026-09-19 电子招标投标示范文本
 * 1143 行中 5 行 ' '/'\t' 锚文本被误判「格式不正确」致保存被拦）。
 */
import { describe, expect, it } from 'vitest';

import {
  collectRowErrors,
  emptyPlaceholder,
  KEY_PATTERN,
  trimRows,
} from './placeholder-table';

const row = (over: Partial<Parameters<typeof trimRows>[0][number]> = {}) => ({
  ...emptyPlaceholder(),
  key: 'project_name',
  name: '项目名称',
  anchor: '招标人：',
  addr: 'para:0',
  ...over,
});

describe('trimRows', () => {
  it('纯空白锚文本原样保留（留白位语义，不被 trim 毁掉）', () => {
    expect(trimRows([row({ anchor: ' ' })])[0].anchor).toBe(' ');
    expect(trimRows([row({ anchor: '\t' })])[0].anchor).toBe('\t');
    expect(trimRows([row({ anchor: '   \n ' })])[0].anchor).toBe('   \n ');
  });

  it('有非空白内容的锚文本去首尾空白', () => {
    expect(trimRows([row({ anchor: '  招标人：  ' })])[0].anchor).toBe(
      '招标人：',
    );
  });

  it('空锚文本保持空串', () => {
    expect(trimRows([row({ anchor: '' })])[0].anchor).toBe('');
  });

  it('key/name/检索词统一 trim；retrieval_query 缺省按空串处理', () => {
    const r = trimRows([
      row({
        key: ' project_name ',
        name: ' 项目名称 ',
        retrieval_query: undefined as unknown as string,
      }),
    ])[0];
    expect(r.key).toBe('project_name');
    expect(r.name).toBe('项目名称');
    expect(r.retrieval_query).toBe('');
  });
});

describe('collectRowErrors', () => {
  it('合法行（含带 addr 的纯空白留白位）零错误', () => {
    const errors = collectRowErrors([
      row(),
      row({ anchor: ' ', addr: 'para:3' }),
      row({ anchor: '\t', addr: 'para:5' }),
    ]);
    expect(errors).toEqual({});
  });

  it('空锚文本无论有无 addr 均标红', () => {
    expect(Object.keys(collectRowErrors([row({ anchor: '' })]))).toEqual([
      '0-anchor',
    ]);
    expect(
      Object.keys(collectRowErrors([row({ anchor: '', addr: 'para:0' })])),
    ).toEqual(['0-anchor']);
  });

  it('纯空白锚文本无 addr（手动行）标红——后端要靠可见锚文本反查定位', () => {
    const errors = collectRowErrors([row({ anchor: ' ', addr: '' })]);
    expect(Object.keys(errors)).toEqual(['0-anchor']);
  });

  it.each([
    ['大写字母', 'Project_name'],
    ['数字开头', '1_project'],
    ['下划线开头', '_project'],
    ['中文 key', '项目名称'],
    ['全角假名', 'ｋｅｙ'],
    ['含连字符', 'project-name'],
    ['超长 65 字符', 'a'.repeat(65)],
    ['空 key', ''],
  ])('非法 key（%s）标红', (_label, key) => {
    expect(Object.keys(collectRowErrors([row({ key })]))).toContain('0-key');
  });

  it('key 边界：字母开头 64 字符合法（含纯数字后缀）', () => {
    expect(KEY_PATTERN.test('a'.repeat(64))).toBe(true);
    expect(KEY_PATTERN.test('a1'.padEnd(64, '9'))).toBe(true);
    expect(
      Object.keys(collectRowErrors([row({ key: 'a'.repeat(64) })])),
    ).not.toContain('0-key');
  });

  it('中文名缺失/纯空白标红', () => {
    expect(Object.keys(collectRowErrors([row({ name: '' })]))).toContain(
      '0-name',
    );
    // trimRows 先行归一化后纯空白变空串再校验
    const trimmed = trimRows([row({ name: '  ' })]);
    expect(Object.keys(collectRowErrors(trimmed))).toContain('0-name');
  });

  it('错误按行索引隔离，多行只标坏行', () => {
    const errors = collectRowErrors([row(), row({ key: 'BAD' }), row()]);
    expect(Object.keys(errors)).toEqual(['1-key']);
  });

  it('对抗性：空行（全空字段）标出 key/name/anchor 三项', () => {
    const errors = collectRowErrors([emptyPlaceholder()]);
    expect(Object.keys(errors).sort()).toEqual(['0-anchor', '0-key', '0-name']);
  });
});
