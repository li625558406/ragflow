import type { NodeKey, SerializedTextNode } from 'lexical';
import { TextNode } from 'lexical';

export interface SerializedMentionNode extends SerializedTextNode {
  userId: string;
  userName: string;
}

export class MentionNode extends TextNode {
  __userId: string;
  __userName: string;

  static getType(): string {
    return 'mention';
  }

  static clone(node: MentionNode): MentionNode {
    return new MentionNode(node.__userId, node.__userName, node.__key);
  }

  constructor(userId: string, userName: string, key?: NodeKey) {
    super(`@${userName}`, key);
    this.__userId = userId;
    this.__userName = userName;
  }

  static importJSON(json: SerializedMentionNode): MentionNode {
    const node = new MentionNode(json.userId, json.userName);
    node.setFormat(json.format);
    node.setStyle(json.style);
    return node;
  }

  exportJSON(): SerializedMentionNode {
    return {
      ...super.exportJSON(),
      type: 'mention',
      userId: this.__userId,
      userName: this.__userName,
      version: 1,
    };
  }

  createDOM(): HTMLElement {
    const span = document.createElement('span');
    span.className = 'text-blue-600 bg-blue-50 px-0.5 rounded font-medium';
    span.textContent = this.__text;
    return span;
  }

  updateDOM(_prevNode: MentionNode, dom: HTMLElement): boolean {
    dom.textContent = this.__text;
    return false;
  }

  canInsertTextBefore(): boolean {
    return true;
  }

  canInsertTextAfter(): boolean {
    return true;
  }

  isTextEntity(): true {
    return true;
  }
}

export function $isMentionNode(node: unknown): node is MentionNode {
  return node instanceof MentionNode;
}

export function $createMentionNode(
  userId: string,
  userName: string,
): MentionNode {
  return new MentionNode(userId, userName);
}
