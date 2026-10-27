"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { TerminalSquare, Trash2, ChevronRight } from "lucide-react";

interface Line {
  id: number;
  type: "input" | "stdout" | "stderr" | "info" | "exit";
  text: string;
}

const WS_URL = process.env.NEXT_PUBLIC_WS_URL ?? "ws://localhost:8000";

export function TerminalPanel() {
  const [lines, setLines] = useState<Line[]>([
    { id: 0, type: "info", text: "AgentOS Terminal — connected to /Volumes/IDK/AgentOS testing\n" },
  ]);
  const [input, setInput] = useState("");
  const [cwd, setCwd] = useState("/Volumes/IDK/AgentOS testing");
  const [busy, setBusy] = useState(false);
  const [history, setHistory] = useState<string[]>([]);
  const [historyIdx, setHistoryIdx] = useState(-1);
  const wsRef = useRef<WebSocket | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const idRef = useRef(1);

  const addLine = useCallback((type: Line["type"], text: string) => {
    setLines((prev) => [...prev, { id: idRef.current++, type, text }]);
  }, []);

  // Connect WebSocket
  useEffect(() => {
    const connect = () => {
      const ws = new WebSocket(`${WS_URL}/ws/terminal`);
      wsRef.current = ws;

      ws.onopen = () => addLine("info", "[connected]\n");

      ws.onmessage = (e) => {
        try {
          const msg = JSON.parse(e.data);
          if (msg.type === "stdout") addLine("stdout", msg.data);
          else if (msg.type === "stderr") addLine("stderr", msg.data);
          else if (msg.type === "exit") {
            if (msg.cwd) setCwd(msg.cwd);
            setBusy(false);
          }
        } catch {
          addLine("stdout", e.data);
        }
      };

      ws.onclose = () => {
        addLine("info", "[disconnected — reconnecting in 2s]\n");
        setBusy(false);
        setTimeout(connect, 2000);
      };

      ws.onerror = () => ws.close();
    };

    connect();
    return () => wsRef.current?.close();
  }, [addLine]);

  // Auto-scroll
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [lines]);

  const send = useCallback(() => {
    const cmd = input.trim();
    if (!cmd || busy || wsRef.current?.readyState !== WebSocket.OPEN) return;

    addLine("input", `${cmd}\n`);
    setHistory((h) => [cmd, ...h.slice(0, 49)]);
    setHistoryIdx(-1);
    setInput("");
    setBusy(true);
    wsRef.current!.send(JSON.stringify({ cmd }));
  }, [input, busy, addLine]);

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") { send(); return; }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      const idx = Math.min(historyIdx + 1, history.length - 1);
      setHistoryIdx(idx);
      setInput(history[idx] ?? "");
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      const idx = Math.max(historyIdx - 1, -1);
      setHistoryIdx(idx);
      setInput(idx === -1 ? "" : history[idx]);
    }
  };

  const clear = () => setLines([{ id: idRef.current++, type: "info", text: "Screen cleared.\n" }]);

  const prompt = `${cwd.split("/").pop() ?? cwd} $ `;

  return (
    <div
      className="flex flex-col h-full bg-[#0d1117] rounded-xl border border-white/10 overflow-hidden font-mono text-[13px]"
      onClick={() => inputRef.current?.focus()}
    >
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2 bg-[#161b22] border-b border-white/10 shrink-0">
        <div className="flex items-center gap-2 text-emerald-400">
          <TerminalSquare size={14} />
          <span className="text-xs font-semibold tracking-wide">TERMINAL</span>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-[11px] text-white/30 max-w-[260px] truncate">{cwd}</span>
          <button onClick={clear} className="text-white/30 hover:text-white/70 transition-colors">
            <Trash2 size={13} />
          </button>
        </div>
      </div>

      {/* Output */}
      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-0 leading-5 scroll-pb-2">
        {lines.map((line) => (
          <div key={line.id} className="whitespace-pre-wrap break-all">
            {line.type === "input" && (
              <span>
                <span className="text-emerald-400">{prompt}</span>
                <span className="text-white">{line.text}</span>
              </span>
            )}
            {line.type === "stdout" && <span className="text-gray-200">{line.text}</span>}
            {line.type === "stderr" && <span className="text-red-400">{line.text}</span>}
            {line.type === "info"   && <span className="text-white/30 text-[11px]">{line.text}</span>}
          </div>
        ))}
        <div ref={bottomRef} />
      </div>

      {/* Input row */}
      <div className="flex items-center gap-2 px-4 py-2 border-t border-white/10 bg-[#0d1117] shrink-0">
        <span className="text-emerald-400 shrink-0">{prompt}</span>
        <input
          ref={inputRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKeyDown}
          disabled={busy}
          placeholder={busy ? "running…" : "type a command…"}
          autoFocus
          className="flex-1 bg-transparent text-white outline-none placeholder-white/20 caret-emerald-400"
        />
        {busy && (
          <span className="text-[10px] text-emerald-400 animate-pulse">●</span>
        )}
        <button
          onClick={send}
          disabled={busy}
          className="text-white/30 hover:text-emerald-400 transition-colors"
        >
          <ChevronRight size={14} />
        </button>
      </div>
    </div>
  );
}
