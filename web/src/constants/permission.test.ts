import { describe, expect, it } from 'vitest';

import {
  getRequiredPermission,
  isSuperuserOnlyPath,
} from '@/constants/permission';

describe('isSuperuserOnlyPath', () => {
  it('命中仅超管前缀（完整路径段）', () => {
    expect(isSuperuserOnlyPath('/template-fill')).toBe(true);
    expect(isSuperuserOnlyPath('/template-fill/detail/abc')).toBe(true);
    expect(isSuperuserOnlyPath('/template-fill/tasks')).toBe(true);
    expect(isSuperuserOnlyPath('/agents')).toBe(true);
    expect(isSuperuserOnlyPath('/agents/123/versions')).toBe(true);
    expect(isSuperuserOnlyPath('/agent-templates')).toBe(true);
    expect(isSuperuserOnlyPath('/agent')).toBe(true);
    expect(isSuperuserOnlyPath('/agent/123')).toBe(true);
    expect(isSuperuserOnlyPath('/memories')).toBe(true);
    expect(isSuperuserOnlyPath('/memory/abc/messages')).toBe(true);
    expect(isSuperuserOnlyPath('/memory-message/1')).toBe(true);
    expect(isSuperuserOnlyPath('/memory-setting')).toBe(true);
    expect(isSuperuserOnlyPath('/smart-crawler')).toBe(true);
    expect(isSuperuserOnlyPath('/permission')).toBe(true);
  });

  it('对抗性：/agent 不误匹配 /agents 之外的相似路径', () => {
    // 段边界：/agentxyz 不是 /agent 的子路径
    expect(isSuperuserOnlyPath('/agentxyz')).toBe(false);
    expect(isSuperuserOnlyPath('/memoriesx')).toBe(false);
    expect(isSuperuserOnlyPath('/permissions')).toBe(false);
  });

  it('C 端与已授权 B 端路径不命中', () => {
    expect(isSuperuserOnlyPath('/')).toBe(false);
    expect(isSuperuserOnlyPath('/home')).toBe(false);
    expect(isSuperuserOnlyPath('/login')).toBe(false);
    expect(isSuperuserOnlyPath('/datasets')).toBe(false);
    expect(isSuperuserOnlyPath('/chats')).toBe(false);
    expect(isSuperuserOnlyPath('/files')).toBe(false);
    expect(isSuperuserOnlyPath('/user-setting/profile')).toBe(false);
  });

  it('对抗性：空串与畸形输入', () => {
    expect(isSuperuserOnlyPath('')).toBe(false);
    expect(isSuperuserOnlyPath('/template-fill/')).toBe(true);
    // 前缀注入：非段边界前缀不算命中
    expect(isSuperuserOnlyPath('/template-fillxxx')).toBe(false);
    expect(isSuperuserOnlyPath('//agents')).toBe(false);
  });
});

describe('getRequiredPermission（超管模块条目已移除）', () => {
  it('超管模块路径不再返回权限点（改走 isSuperuserOnlyPath）', () => {
    expect(getRequiredPermission('/agents')).toBeUndefined();
    expect(getRequiredPermission('/memories')).toBeUndefined();
    expect(getRequiredPermission('/smart-crawler')).toBeUndefined();
    expect(getRequiredPermission('/permission')).toBeUndefined();
    expect(getRequiredPermission('/template-fill')).toBeUndefined();
  });

  it('RBAC 模块路径仍返回权限点', () => {
    expect(getRequiredPermission('/datasets')).toBe('dataset');
    expect(getRequiredPermission('/dataset/abc')).toBe('dataset');
    expect(getRequiredPermission('/chats')).toBe('chat');
    expect(getRequiredPermission('/searches')).toBe('search');
    expect(getRequiredPermission('/files')).toBe('file');
    expect(getRequiredPermission('/user-setting')).toBe('user_setting');
  });

  it('对抗性：/datasets 不误匹配 /dataset 段边界', () => {
    expect(getRequiredPermission('/datasetx')).toBeUndefined();
  });
});
