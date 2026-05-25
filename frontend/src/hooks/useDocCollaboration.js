import { useEffect, useRef, useCallback } from "react";

const WS_URL = import.meta.env.VITE_AI_SERVICE_URL?.replace("https://", "wss://")
                                                    ?.replace("http://", "ws://");

export function useDocCollaboration({
  docName,
  orgId,
  email,
  token,
  onCursor,
  onAnnotationEvent,
  onPresence,
}) {
  const wsRef       = useRef(null);
  const pingRef     = useRef(null);
  const reconnectRef = useRef(null);
  const connectRef   = useRef(null);

  const connect = useCallback(() => {
    if (!docName || !orgId || !token) return;

    const url = `${WS_URL}/ws/doc/${encodeURIComponent(docName)}`
      + `?token=${encodeURIComponent(token)}`
      + `&org_id=${encodeURIComponent(orgId)}`
      + `&email=${encodeURIComponent(email || "anonymous")}`;

    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      console.log("[WS] Connected to doc:", docName);
      // Ping every 25s to keep connection alive
      pingRef.current = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: "ping" }));
        }
      }, 25000);
    };

    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        if (msg.type === "cursor")     onCursor?.(msg);
        if (msg.type === "annotation") onAnnotationEvent?.(msg);
        if (msg.type === "presence")   onPresence?.(msg);
      } catch { /* ignore */ }
    };

    ws.onclose = () => {
      clearInterval(pingRef.current);
      // Auto-reconnect after 3s
      reconnectRef.current = setTimeout(() => connectRef.current?.(), 3000);
    };

    ws.onerror = (e) => {
      console.warn("[WS] Error:", e);
      ws.close();
    };
  }, [docName, orgId, email, token, onCursor, onAnnotationEvent, onPresence]);

  useEffect(() => {
    connectRef.current = connect;
  }, [connect]);

  useEffect(() => {
    connect();
    return () => {
      clearTimeout(reconnectRef.current);
      clearInterval(pingRef.current);
      wsRef.current?.close();
    };
  }, [connect]);

  const sendCursor = useCallback((x, y, page) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: "cursor", x, y, page }));
    }
  }, []);

  const sendAnnotationEvent = useCallback((action, data) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: "annotation", action, data }));
    }
  }, []);

  return { sendCursor, sendAnnotationEvent };
}