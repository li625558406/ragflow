import { UniverDocsCorePreset } from '@univerjs/preset-docs-core';
import '@univerjs/preset-docs-core/lib/index.css';
import UniverDocsCoreZhCN from '@univerjs/preset-docs-core/locales/zh-CN';
import { UniverDocsDrawingPreset } from '@univerjs/preset-docs-drawing';
import UniverDocsDrawingZhCN from '@univerjs/preset-docs-drawing/locales/zh-CN';
import { UniverDocsThreadCommentPreset } from '@univerjs/preset-docs-thread-comment';
import UniverDocsThreadCommentZhCN from '@univerjs/preset-docs-thread-comment/locales/zh-CN';

/** Docs 场景下挂载的 zh-CN locale 合集 */
export const DOCS_LOCALES = {
  ...UniverDocsCoreZhCN,
  ...UniverDocsDrawingZhCN,
  ...UniverDocsThreadCommentZhCN,
};

/** Docs 场景下挂载的 preset 清单 */
export const DOCS_PRESETS = (container: HTMLElement) => [
  UniverDocsCorePreset({ container }),
  UniverDocsDrawingPreset(),
  UniverDocsThreadCommentPreset(),
];
