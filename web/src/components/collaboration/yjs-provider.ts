/**
 * Minimal Yjs WebSocket provider — blind relay compatible.
 *
 * From first principles: the server is a dumb pipe. The provider
 * encodes Yjs document updates and awareness states as JSON messages,
 * sends them over WebSocket, and applies incoming messages.
 *
 * Implements the Provider interface expected by @lexical/yjs's createBinding.
 */
import type { Doc } from 'yjs';
import * as Y from 'yjs';

// ── Types ──────────────────────────────────────────────────────────────

export interface UserState {
  anchorPos: null | Y.RelativePosition;
  color: string;
  focusing: boolean;
  focusPos: null | Y.RelativePosition;
  name: string;
  awarenessData: object;
  [key: string]: unknown;
}

export interface ProviderAwareness {
  getLocalState: () => UserState | null;
  getStates: () => Map<number, UserState>;
  off: (type: 'update', cb: () => void) => void;
  on: (type: 'update', cb: () => void) => () => void;
  setLocalState: (arg0: UserState) => void;
  setLocalStateField: (field: string, value: unknown) => void;
}

// ── Awareness store ────────────────────────────────────────────────────

class AwarenessStore implements ProviderAwareness {
  private states: Map<number, UserState> = new Map();
  private localClientID: number;
  private listeners: Array<() => void> = [];
  /** Called when local state changes — provider hooks in to broadcast */
  onLocalChange: ((stateJSON: string) => void) | null = null;

  constructor(localClientID: number) {
    this.localClientID = localClientID;
  }

  getLocalState(): UserState | null {
    return this.states.get(this.localClientID) ?? null;
  }

  getStates(): Map<number, UserState> {
    return new Map(this.states);
  }

  setLocalState(state: UserState): void {
    this.states.set(this.localClientID, state);
    this._emit();
    this._broadcastLocal();
  }

  setLocalStateField(field: string, value: unknown): void {
    const current = this.states.get(this.localClientID) ?? {
      anchorPos: null,
      color: '#958DF1',
      focusing: false,
      focusPos: null,
      name: '',
      awarenessData: {},
    };
    (current as Record<string, unknown>)[field] = value;
    this.states.set(this.localClientID, current);
    this._emit();
    if (field !== 'anchorPos' && field !== 'focusPos') {
      // Don't broadcast on every cursor move — throttle in the provider
    }
  }

  /** Apply a remote user's state from a JSON aw message */
  setRemoteState(data: {
    clientID: number;
    name: string;
    color: string;
    focusing?: boolean;
    anchorPos?: unknown;
    focusPos?: unknown;
  }): void {
    const state: UserState = {
      anchorPos: data.anchorPos
        ? Y.createRelativePositionFromJSON(data.anchorPos)
        : null,
      color: data.color,
      focusing: data.focusing ?? false,
      focusPos: data.focusPos
        ? Y.createRelativePositionFromJSON(data.focusPos)
        : null,
      name: data.name,
      awarenessData: {},
    };
    this.states.set(data.clientID, state);
    this._emit();
  }

  /** Remove all state for a disconnected client */
  removeRemoteState(clientID: number): void {
    this.states.delete(clientID);
    this._emit();
  }

  on(_type: 'update', cb: () => void): () => void {
    this.listeners.push(cb);
    return () => {
      this.listeners = this.listeners.filter((l) => l !== cb);
    };
  }

  off(_type: 'update', cb: () => void): void {
    this.listeners = this.listeners.filter((l) => l !== cb);
  }

  /** Serialize local state for WebSocket broadcast */
  encodeLocalState(): string | null {
    const state = this.states.get(this.localClientID);
    if (!state) return null;
    return JSON.stringify({
      clientID: this.localClientID,
      name: state.name,
      color: state.color,
      focusing: state.focusing,
      anchorPos: state.anchorPos
        ? Y.relativePositionToJSON(state.anchorPos)
        : null,
      focusPos: state.focusPos
        ? Y.relativePositionToJSON(state.focusPos)
        : null,
    });
  }

  private _broadcastLocal(): void {
    if (!this.onLocalChange) return;
    const encoded = this.encodeLocalState();
    if (encoded) {
      this.onLocalChange(encoded);
    }
  }

  private _emit(): void {
    for (const cb of this.listeners) {
      cb();
    }
  }
}

// ── WebSocket Provider ──────────────────────────────────────────────────

export class CollaborationWebSocketProvider {
  public awareness: AwarenessStore;
  public readonly doc: Doc;

  private docId: string;
  private token: string;
  private baseUrl: string;
  private ws: WebSocket | null = null;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private reconnectDelay = 1000;
  private maxReconnectDelay = 30000;
  private synced = false;
  private closed = false;

  // Local buffer for edits while WebSocket is disconnected
  private offlineBuffer: Array<string> = [];

  // Awareness broadcast throttle
  private awThrottleTimer: ReturnType<typeof setTimeout> | null = null;
  // Awareness heartbeat — re-send local awareness periodically so late joiners see us
  private awHeartbeatTimer: ReturnType<typeof setInterval> | null = null;

  // Event listeners
  private syncListeners: Array<(isSynced: boolean) => void> = [];
  private statusListeners: Array<(arg0: { status: string }) => void> = [];
  private updateListeners: Array<(arg0: unknown) => void> = [];
  private savedListeners: Array<(arg0: { userName: string }) => void> = [];
  private commentChangedListeners: Array<() => void> = [];

  constructor(doc: Doc, docId: string, token: string, baseUrl?: string) {
    this.doc = doc;
    this.docId = docId;
    this.token = token;
    this.baseUrl = baseUrl ?? '';

    this.awareness = new AwarenessStore(doc.clientID);

    // Broadcast awareness changes (throttled to avoid flooding on cursor moves)
    this.awareness.onLocalChange = () => {
      if (this.awThrottleTimer) return;
      this.awThrottleTimer = setTimeout(() => {
        this.awThrottleTimer = null;
        const encoded = this.awareness.encodeLocalState();
        if (encoded && this.ws?.readyState === WebSocket.OPEN) {
          this.ws.send(JSON.stringify({ t: 'aw', d: encoded }));
        }
      }, 50); // 50ms throttle
    };
  }

  // ── Public API (compatible with @lexical/yjs Provider) ────────────

  connect(): void {
    console.log(
      '[YjsProvider] connect() called, docId=',
      this.docId,
      'yjsClientID=',
      this.doc.clientID,
      'wasClosed=',
      this.closed,
    );
    this.closed = false;
    // Fix stale binding from React StrictMode remount.
    // CollaborationPlugin's root.destroy() clears collabNodeMap but does NOT
    // clear _collabNode references on child shared types (Y.Map / Y.XmlText).
    // Stale _collabNode pointers cause $syncEvent to use destroyed CollabNodes
    // whose _children array is out of sync with the Yjs state, so incremental
    // remote edits fail to propagate to the Lexical tree while the initial
    // full-state init appears to work.
    try {
      const rootXmlText = this.doc.get('root', Y.XmlText);
      for (const [, sharedType] of this.doc.share.entries()) {
        if (sharedType === rootXmlText) continue; // root is re-assigned by createBinding
        // @ts-expect-error accessing Yjs internal property
        sharedType._collabNode = undefined;
      }
    } catch {
      // Non-critical — binding may not be created yet
    }
    this._connect();
  }

  disconnect(): void {
    console.log(
      '[YjsProvider] disconnect() called, docId=',
      this.docId,
      'yjsClientID=',
      this.doc.clientID,
      'wasClosed=',
      this.closed,
      'wsState=',
      this._debugWsState(),
    );
    this.closed = true;
    this._cleanup();
    console.log(
      '[YjsProvider] disconnect() done, wsState after=',
      this._debugWsState(),
    );
  }

  /** Send full Yjs document state as a save snapshot to the server. */
  sendFullState(): void {
    if (this.ws?.readyState !== WebSocket.OPEN) return;
    const fullState = Y.encodeStateAsUpdate(this.doc);
    const b64 = uint8ArrayToBase64(fullState);
    this.ws.send(JSON.stringify({ t: 'save', d: b64 }));
  }

  /** Debug helper: returns the current WebSocket state as a readable string. */
  _debugWsState(): string {
    if (!this.ws) return 'no-ws';
    switch (this.ws.readyState) {
      case WebSocket.CONNECTING:
        return 'CONNECTING';
      case WebSocket.OPEN:
        return 'OPEN';
      case WebSocket.CLOSING:
        return 'CLOSING';
      case WebSocket.CLOSED:
        return 'CLOSED';
      default:
        return `UNKNOWN(${this.ws.readyState})`;
    }
  }

  /**
   * Notify other clients that comments changed so they reload.
   * Server relays this to all other clients in the room (excludes sender).
   * Receiver re-fetches via REST /comments endpoint — keeps server authoritative.
   */
  notifyCommentChanged(): void {
    if (this.ws?.readyState !== WebSocket.OPEN) return;
    this.ws.send(JSON.stringify({ t: 'comment-changed' }));
  }

  on(type: string, cb: (...args: unknown[]) => void): void {
    switch (type) {
      case 'sync':
        this.syncListeners.push(cb as (isSynced: boolean) => void);
        break;
      case 'status':
        this.statusListeners.push(cb as (arg0: { status: string }) => void);
        break;
      case 'update':
        this.updateListeners.push(cb as (arg0: unknown) => void);
        break;
      case 'saved':
        this.savedListeners.push(cb as (arg0: { userName: string }) => void);
        break;
      case 'comment-changed':
        this.commentChangedListeners.push(cb as () => void);
        break;
      case 'reload':
        // Supported for @lexical/react CollaborationPlugin compatibility
        // No-op: we don't trigger doc reloads
        break;
    }
  }

  off(type: string, cb: (...args: unknown[]) => void): void {
    switch (type) {
      case 'sync':
        this.syncListeners = this.syncListeners.filter((l) => l !== cb);
        break;
      case 'status':
        this.statusListeners = this.statusListeners.filter((l) => l !== cb);
        break;
      case 'update':
        this.updateListeners = this.updateListeners.filter((l) => l !== cb);
        break;
      case 'saved':
        this.savedListeners = this.savedListeners.filter((l) => l !== cb);
        break;
      case 'comment-changed':
        this.commentChangedListeners = this.commentChangedListeners.filter(
          (l) => l !== cb,
        );
        break;
      case 'reload':
        break;
    }
  }

  // ── Internal ──────────────────────────────────────────────────────

  private _connect = (): void => {
    if (this.closed) return;

    // Register doc update listener (re-registered on every connect
    // because _cleanup() removes it on disconnect)
    this.doc.on('update', this._onDocUpdate);

    const wsUrl = this._buildUrl();
    const ws = new WebSocket(wsUrl);
    this.ws = ws;
    console.log(
      '[YjsProvider] new WebSocket created, docId=',
      this.docId,
      'yjsClientID=',
      this.doc.clientID,
    );

    ws.onopen = () => {
      console.log(
        '[YjsProvider] ws.onopen, docId=',
        this.docId,
        'yjsClientID=',
        this.doc.clientID,
      );
      this.reconnectDelay = 1000;
      this._emitStatus('connected');
      // Flush any edits buffered while offline
      this._flushOfflineBuffer();
      // Send initial awareness state
      const encoded = this.awareness.encodeLocalState();
      if (encoded) {
        ws.send(JSON.stringify({ t: 'aw', d: encoded }));
      }
      // Start awareness heartbeat — re-send every 5s so late joiners see us
      if (this.awHeartbeatTimer) clearInterval(this.awHeartbeatTimer);
      this.awHeartbeatTimer = setInterval(() => {
        const enc = this.awareness.encodeLocalState();
        if (enc && this.ws?.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ t: 'aw', d: enc }));
        }
      }, 5000);
    };

    ws.onmessage = (event: MessageEvent) => {
      try {
        const msg = JSON.parse(event.data as string) as {
          t: string;
          d: unknown;
          ro?: boolean;
        };
        this._handleMessage(msg);
      } catch {
        // ignore malformed messages
      }
    };

    ws.onclose = () => {
      console.log(
        '[YjsProvider] ws.onclose, docId=',
        this.docId,
        'yjsClientID=',
        this.doc.clientID,
        'closed=',
        this.closed,
      );
      this.ws = null;
      this._emitStatus('disconnected');
      this._scheduleReconnect();
    };

    ws.onerror = () => {
      // onclose will fire after onerror
    };
  };

  private _buildUrl = (): string => {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    // Use dedicated WS host if set (bypasses Vite proxy which breaks WebSocket for remote backends)
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const wsHost = (import.meta as any).env?.VITE_WS_HOST || '';
    const host = this.baseUrl || wsHost || location.host;
    return `${proto}//${host}/api/v1/collaboration/ws/${this.docId}?token=${encodeURIComponent(this.token)}`;
  };

  private _handleMessage = (msg: {
    t: string;
    d: unknown;
    ro?: boolean;
  }): void => {
    switch (msg.t) {
      case 'init': {
        console.log(
          '[YjsProvider] init received, data:',
          msg.d ? `len=${(msg.d as string).length}` : 'null',
          'ro=' + (msg.ro === true ? 'READ_ONLY' : 'editable'),
        );
        if (msg.d) {
          try {
            // Clear stale _collabNode refs before applying server state.
            // On reconnection (e.g. after HMR or tab refocus), the binding may
            // hold destroyed CollabNodes whose _children are out of sync.
            try {
              const rootXmlText = this.doc.get('root', Y.XmlText);
              for (const [, sharedType] of this.doc.share.entries()) {
                if (sharedType === rootXmlText) continue;
                // @ts-expect-error accessing Yjs internal property
                sharedType._collabNode = undefined;
              }
            } catch {
              // Non-critical
            }
            const update = base64ToUint8Array(msg.d as string);
            Y.applyUpdate(this.doc, update, 'ws-init');
            console.log(
              '[YjsProvider] init applied, doc keys:',
              Array.from(this.doc.share.keys()),
            );
          } catch (e) {
            console.error('[YjsProvider] Failed to apply init state:', e);
          }
        }
        // Server state is now authoritative — discard stale offline edits
        this.offlineBuffer.length = 0;
        if (!this.synced) {
          this.synced = true;
          for (const cb of this.syncListeners) {
            cb(true);
          }
        }
        break;
      }

      case 'update': {
        try {
          const update = base64ToUint8Array(msg.d as string);
          Y.applyUpdate(this.doc, update, 'ws-remote');
        } catch (e) {
          console.error('[YjsProvider] Failed to apply update:', e);
        }
        break;
      }

      case 'aw': {
        try {
          const data = JSON.parse(msg.d as string) as {
            clientID: number;
            name: string;
            color: string;
            focusing?: boolean;
            anchorPos?: unknown;
            focusPos?: unknown;
          };
          console.log('[Collab] recv aw from client', data.clientID, data.name);
          this.awareness.setRemoteState(data);
        } catch {
          // ignore invalid awareness data
        }
        break;
      }

      case 'presence': {
        // Server broadcasts online member list — use it to clean up stale awareness
        try {
          const onlineList = msg.d as Array<{ uid: string; name: string }>;
          if (onlineList.length === 0) {
            // No one else online — remove all remote states
            this.awareness.getStates().forEach((_state, clientID) => {
              if (clientID !== this.doc.clientID) {
                this.awareness.removeRemoteState(clientID);
              }
            });
          } else if (this.ws?.readyState === WebSocket.OPEN) {
            // Other users present — immediately re-broadcast own awareness so
            // they see us without waiting for the 5s heartbeat. This closes the
            // race where our last_aw wasn't cached on server when they joined.
            const enc = this.awareness.encodeLocalState();
            if (enc) {
              this.ws.send(JSON.stringify({ t: 'aw', d: enc }));
            }
          }
        } catch {
          // ignore invalid presence data
        }
        break;
      }

      case 'aw-remove': {
        // Server tells us a specific client disconnected — remove their awareness
        try {
          const removedClientID = msg.d as number;
          this.awareness.removeRemoteState(removedClientID);
        } catch {
          // ignore
        }
        break;
      }

      case 'saved': {
        // Another client saved the document — notify listeners
        try {
          const data = msg.d as { name: string };
          for (const cb of this.savedListeners) {
            cb({ userName: data.name || 'Unknown' });
          }
        } catch {
          // ignore
        }
        break;
      }

      case 'comment-changed': {
        // Another client added/edited/deleted a comment — notify CommentPanel
        // to reload via REST. cb has no args; receiver decides what to fetch.
        for (const cb of this.commentChangedListeners) {
          cb();
        }
        break;
      }

      case 'error': {
        console.error('[YjsProvider] Server error:', msg.d);
        break;
      }

      case 'force-reload': {
        // Server-side mutation (e.g. version restore) overwrote the persisted
        // ydoc. The server has already dropped its in-memory room state, so a
        // plain page reload will pick up the fresh content on reconnect.
        console.log('[YjsProvider] force-reload received, reloading page');
        window.location.reload();
        break;
      }
    }
  };

  private _onDocUpdate = (update: Uint8Array, origin: unknown): void => {
    // Don't re-broadcast updates from remote
    if (origin === 'ws-init' || origin === 'ws-remote') return;

    const b64 = uint8ArrayToBase64(update);
    const msg = JSON.stringify({ t: 'update', d: b64 });
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(msg);
    } else {
      // Buffer locally when WebSocket is disconnected
      this.offlineBuffer.push(msg);
      if (this.offlineBuffer.length > 500) {
        // Prevent unbounded growth — drop oldest
        this.offlineBuffer = this.offlineBuffer.slice(-200);
      }
    }
  };

  /** Flush buffered edits after WebSocket reconnects. */
  private _flushOfflineBuffer(): void {
    if (this.offlineBuffer.length === 0) return;
    const pending = this.offlineBuffer.splice(0);
    for (const msg of pending) {
      try {
        if (this.ws?.readyState === WebSocket.OPEN) {
          this.ws.send(msg);
        }
      } catch {
        // Connection may have closed between check and send — discard
      }
    }
  }

  private _scheduleReconnect = (): void => {
    if (this.closed || this.reconnectTimer) return;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.reconnectDelay = Math.min(
        this.reconnectDelay * 2,
        this.maxReconnectDelay,
      );
      this._connect();
    }, this.reconnectDelay);
  };

  private _emitStatus = (status: string): void => {
    for (const cb of this.statusListeners) {
      cb({ status });
    }
  };

  private _cleanup = (): void => {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.awThrottleTimer) {
      clearTimeout(this.awThrottleTimer);
      this.awThrottleTimer = null;
    }
    if (this.awHeartbeatTimer) {
      clearInterval(this.awHeartbeatTimer);
      this.awHeartbeatTimer = null;
    }
    this.doc.off('update', this._onDocUpdate);
    if (this.ws) {
      console.log(
        '[YjsProvider] _cleanup calling ws.close(), docId=',
        this.docId,
        'yjsClientID=',
        this.doc.clientID,
        'wsState=',
        this._debugWsState(),
      );
      // Send explicit leave notice BEFORE closing so the server removes this
      // client from the room immediately. Quart's WS close-frame propagation
      // is flaky — without this, the server's recv-timeout (45s) is the only
      // way it learns we're gone. Meanwhile the client thinks the timeout-
      // closed WS was unexpected (closed=false) and reconnects, creating a
      // zombie that permanently inflates the online count.
      try {
        if (this.ws.readyState === WebSocket.OPEN) {
          this.ws.send(JSON.stringify({ t: 'leave' }));
        }
      } catch {
        // Best-effort — if send throws, the close below still works
      }
      try {
        this.ws.close();
      } catch (e) {
        console.error('[YjsProvider] ws.close() threw:', e);
      }
      this.ws = null;
    } else {
      console.log(
        '[YjsProvider] _cleanup no ws to close, docId=',
        this.docId,
        'yjsClientID=',
        this.doc.clientID,
      );
    }
  };
}

// ── Base64 helpers (pure JS, zero dependencies) ─────────────────────────

export function uint8ArrayToBase64(bytes: Uint8Array): string {
  let binary = '';
  const len = bytes.length;
  for (let i = 0; i < len; i++) {
    binary += String.fromCharCode(bytes[i]);
  }
  return btoa(binary);
}

export function base64ToUint8Array(base64: string): Uint8Array {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes;
}
