import type {
  LexicalEditor,
  LexicalNode,
  NodeKey,
  SerializedLexicalNode,
} from 'lexical';
import { $getNodeByKey, DecoratorNode } from 'lexical';
import { useEffect, useState } from 'react';

export interface SerializedImageNode extends SerializedLexicalNode {
  src: string;
  altText: string;
  width: number;
  height: number;
  type: 'image';
  version: 1;
}

function ImageComponent({
  src,
  altText,
  width,
  editor,
  nodeKey,
}: {
  src: string;
  altText: string;
  width: number;
  editor: LexicalEditor;
  nodeKey: NodeKey;
}) {
  const [editing, setEditing] = useState(false);
  const [alt, setAlt] = useState(altText);

  // Sync local state when node is re-created from serialized state (undo/redo)
  useEffect(() => {
    setAlt(altText);
  }, [altText]);

  const applyAltChange = (newAlt: string) => {
    editor.update(() => {
      const node = $getNodeByKey(nodeKey);
      if (node instanceof ImageNode) {
        const writable = node.getWritable();
        (writable as ImageNode).__altText = newAlt;
      }
    });
  };

  return (
    <div className="group relative my-2 inline-block" contentEditable={false}>
      <img
        src={src}
        alt={alt}
        className="max-w-full h-auto rounded-lg"
        style={{ width: width || undefined }}
        draggable={false}
      />
      <div
        className="absolute bottom-1 left-1 opacity-0 group-hover:opacity-100 transition-opacity"
        onClick={() => setEditing(true)}
      >
        {editing ? (
          <input
            className="text-xs bg-black/60 text-white px-1.5 py-0.5 rounded outline-none w-40"
            placeholder="添加描述..."
            value={alt}
            autoFocus
            onChange={(e) => setAlt(e.target.value)}
            onBlur={() => {
              setEditing(false);
              applyAltChange(alt);
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                setEditing(false);
                applyAltChange(alt);
              }
            }}
          />
        ) : alt ? (
          <span className="text-xs bg-black/50 text-white px-1.5 py-0.5 rounded cursor-pointer">
            {alt}
          </span>
        ) : null}
      </div>
    </div>
  );
}

export class ImageNode extends DecoratorNode<JSX.Element> {
  __src: string;
  __altText: string;
  __width: number;
  __height: number;

  static getType(): string {
    return 'image';
  }

  static clone(node: ImageNode): ImageNode {
    return new ImageNode(
      node.__src,
      node.__altText,
      node.__width,
      node.__height,
      node.__key,
    );
  }

  constructor(
    src: string,
    altText: string = '',
    width: number = 0,
    height: number = 0,
    key?: NodeKey,
  ) {
    super(key);
    this.__src = src;
    this.__altText = altText;
    this.__width = width;
    this.__height = height;
  }

  createDOM(): HTMLElement {
    const div = document.createElement('div');
    div.className = 'inline-block';
    return div;
  }

  updateDOM(): false {
    return false;
  }

  decorate(editor: LexicalEditor): JSX.Element {
    return (
      <ImageComponent
        src={this.__src}
        altText={this.__altText}
        width={this.__width}
        editor={editor}
        nodeKey={this.__key}
      />
    );
  }

  exportJSON(): SerializedImageNode {
    return {
      ...super.exportJSON(),
      src: this.__src,
      altText: this.__altText,
      width: this.__width,
      height: this.__height,
      type: 'image',
      version: 1,
    };
  }

  static importJSON(json: SerializedImageNode): ImageNode {
    return new ImageNode(json.src, json.altText, json.width, json.height);
  }
}

export function $createImageNode(
  src: string,
  altText: string = '',
  width: number = 0,
  height: number = 0,
): ImageNode {
  return new ImageNode(src, altText, width, height);
}

export function $isImageNode(
  node: LexicalNode | null | undefined,
): node is ImageNode {
  return node instanceof ImageNode;
}
