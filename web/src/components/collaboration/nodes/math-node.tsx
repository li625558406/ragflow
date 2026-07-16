import katex from 'katex';
import type { LexicalNode, NodeKey, SerializedLexicalNode } from 'lexical';
import { DecoratorNode } from 'lexical';
import { useEffect, useRef } from 'react';

export interface SerializedMathNode extends SerializedLexicalNode {
  equation: string;
  type: 'math';
  version: 1;
}

function MathComponent({ equation }: { equation: string }) {
  const containerRef = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    if (containerRef.current) {
      try {
        katex.render(equation, containerRef.current, {
          throwOnError: false,
          displayMode: false,
        });
      } catch {
        containerRef.current.textContent = equation;
      }
    }
  }, [equation]);

  return (
    <span
      ref={containerRef}
      className="inline-block align-middle px-0.5 select-none"
      contentEditable={false}
    />
  );
}

export class MathNode extends DecoratorNode<JSX.Element> {
  __equation: string;

  static getType(): string {
    return 'math';
  }

  static clone(node: MathNode): MathNode {
    return new MathNode(node.__equation, node.__key);
  }

  constructor(equation: string, key?: NodeKey) {
    super(key);
    this.__equation = equation;
  }

  createDOM(): HTMLElement {
    const span = document.createElement('span');
    span.className = 'inline-block align-middle';
    return span;
  }

  updateDOM(): false {
    return false;
  }

  decorate(): JSX.Element {
    return <MathComponent equation={this.__equation} />;
  }

  exportJSON(): SerializedMathNode {
    return {
      ...super.exportJSON(),
      equation: this.__equation,
      type: 'math',
      version: 1,
    };
  }

  static importJSON(json: SerializedMathNode): MathNode {
    return new MathNode(json.equation);
  }
}

export function $createMathNode(equation: string): MathNode {
  return new MathNode(equation);
}

export function $isMathNode(
  node: LexicalNode | null | undefined,
): node is MathNode {
  return node instanceof MathNode;
}
