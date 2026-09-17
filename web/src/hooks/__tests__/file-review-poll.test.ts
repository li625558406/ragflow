// 文件审核轮询开关（shouldPollFileReview）单测。
//
// 为什么单独测这个纯函数：stale 判定分成两半 —— 服务端算出「已中断」（is_stale_running），
// 前端据此停轮询。漏了后半句，后果是服务重启 / 崩溃后对一个永远不会结束的僵尸轮次每 3s
// 白打一次接口，且卡片永远转圈。这两半必须同时成立，所以两半都要有用例。
// request / api 均为 mock：本文件只测纯判定，不触网也不拉起 axios 层。
jest.mock('@/utils/request', () => ({ __esModule: true, default: {} }));
jest.mock('@/utils/api', () => ({ __esModule: true, default: {} }));

import {
  FILE_REVIEW_POLL_MS,
  shouldPollFileReview,
} from '../use-file-review-request';

describe('shouldPollFileReview', () => {
  it('reviewing / fixing 且未被判中断 → 继续按间隔轮询', () => {
    expect(shouldPollFileReview({ status: 'reviewing', stale: false })).toBe(
      FILE_REVIEW_POLL_MS,
    );
    expect(shouldPollFileReview({ status: 'fixing', stale: false })).toBe(
      FILE_REVIEW_POLL_MS,
    );
  });

  it('stale=true 时即便 status 仍是 reviewing/fixing 也必须停（本功能的核心回归点）', () => {
    // 僵尸轮次的 status 永远不会变终态，只看 status 就会无限轮询 —— 这是
    // 「服务端已判中断、前端却继续打接口」的那个漏洞。
    expect(shouldPollFileReview({ status: 'reviewing', stale: true })).toBe(
      false,
    );
    expect(shouldPollFileReview({ status: 'fixing', stale: true })).toBe(false);
  });

  it('终态（annotated / done / failed）停轮询', () => {
    for (const status of ['annotated', 'done', 'failed']) {
      expect(shouldPollFileReview({ status, stale: false })).toBe(false);
    }
  });

  it('current 为 undefined（首拉未回 / 该文件还没有任何轮次）停轮询', () => {
    expect(shouldPollFileReview(undefined)).toBe(false);
  });

  it('对抗：status 是空串 / 未知值 / null，一律停轮询而不是当成「在跑」', () => {
    // 判据是白名单（isRoundRunning）而不是黑名单：服务端将来新增一个状态时，
    // 默认行为必须是「不轮询」（安全），不能是「轮询」（放大请求）。
    expect(shouldPollFileReview({ status: '', stale: false })).toBe(false);
    expect(
      shouldPollFileReview({ status: 'unknown_state', stale: false }),
    ).toBe(false);
    expect(
      shouldPollFileReview({ status: null as unknown as string, stale: false }),
    ).toBe(false);
  });

  it('对抗：stale 是 undefined（旧服务端不返回该字段）时仍按 status 轮询', () => {
    // 向前兼容：strict 的 !== true 语义 —— 字段缺失按「未中断」处理，
    // 否则灰度期间旧后端会让所有活轮次都不再刷新。
    expect(
      shouldPollFileReview({ status: 'reviewing' } as unknown as {
        status: string;
        stale: boolean;
      }),
    ).toBe(FILE_REVIEW_POLL_MS);
  });
});
