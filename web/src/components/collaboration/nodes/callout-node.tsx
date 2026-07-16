import type { LexicalNode, NodeKey, SerializedElementNode } from 'lexical';
import { ElementNode } from 'lexical';

export type CalloutType = 'info' | 'warning' | 'tip' | 'danger';

const CALLOUT_STYLES: Record<CalloutType, string> = {
  info: 'bg-blue-50 border-blue-300',
  warning: 'bg-amber-50 border-amber-300',
  tip: 'bg-green-50 border-green-300',
  danger: 'bg-red-50 border-red-300',
};

export interface SerializedCalloutNode extends SerializedElementNode {
  calloutType: CalloutType;
  type: 'callout';
  version: 1;
}

export class CalloutNode extends ElementNode {
  __calloutType: CalloutType;

  static getType(): string {
    return 'callout';
  }

  static clone(node: CalloutNode): CalloutNode {
    return new CalloutNode(node.__calloutType, node.__key);
  }

  constructor(calloutType: CalloutType = 'info', key?: NodeKey) {
    super(key);
    this.__calloutType = calloutType;
  }

  createDOM(): HTMLElement {
    const div = document.createElement('div');
    div.className = `border-l-4 rounded-r-lg p-3 my-2 ${CALLOUT_STYLES[this.__calloutType]}`;
    return div;
  }

  updateDOM(): false {
    return false;
  }

  exportJSON(): SerializedCalloutNode {
    return {
      ...super.exportJSON(),
      calloutType: this.__calloutType,
      type: 'callout',
      version: 1,
    };
  }

  static importJSON(json: SerializedCalloutNode): CalloutNode {
    return new CalloutNode(json.calloutType);
  }
}

export function $createCalloutNode(
  calloutType: CalloutType = 'info',
): CalloutNode {
  return new CalloutNode(calloutType);
}

export function $isCalloutNode(
  node: LexicalNode | null | undefined,
): node is CalloutNode {
  return node instanceof CalloutNode;
}
