"use client";

import { useEffect, useRef, useState, memo, useCallback } from "react";
import { useAppStore, Log } from "@/lib/store";
import { Terminal, Pause, Play, ChevronDown, Filter } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";

// ── Agent metadata ────────────────────────────────────────────────────────────
const AGENT_META: Record<string, { color: string; bg: string; border: string; dot: string }> = {
  PLANNER:      { color: "text-blue-400",   bg: "bg-blue-950/40",   border: "border-blue-500/30",  dot: "bg-blue-400"   },
  CODER:        { color: "text-violet-400", bg: "bg-violet-950/40", border: "border-violet-500/30",dot: "bg-violet-400" },
  TESTER:       { color: "text-emerald-400",bg: "bg-emerald-950/40",border: "border-emerald-500/30",dot:"bg-emerald-400"},
  VALIDATOR:    { color: "text-emerald-400",bg: "bg-emerald-950/40",border: "border-emerald-500/30",dot:"bg-emerald-400"},
  DEBUGGER:     { color: "text-rose-400",   bg: "bg-rose-950/40",   border: "border-rose-500/30",   dot: "bg-rose-400"  },
  ORCHESTRATOR: { color: "text-amber-400",  bg: "bg-amber-950/40",  border: "border-amber-500/30",  dot: "bg-amber-400" },
  SYSTEM:       { color: "text-slate-400",  bg: "bg-slate-950/40",  border: "border-slate-500/20",  dot: "bg-slate-400" },
  STREAMING:    { color: "text-cyan-400",   bg: "bg-cyan-950/40",   border: "border-cyan-500/30",   dot: "bg-cyan-400"  },
};

const TYPE_COLOR: Record<string, string> = {
  error:   "text-rose-400",
  warning: "text-amber-400",
  success: "text-emerald-400",
  info:    "text-slate-300",
};

function parseLog(message: string): { agent: string; text: string } {
  const match = message.match(/^\[(.*?)\]\s(.*)/);
  if (match) return { agent: match[1].toUpperCase(), text: match[2] };
  if (message.includes("➜ [RUNNING]")) return { agent: "ORCHESTRATOR", text: message };
  if (message.includes("✗ ["))        return { agent: "DEBUGGER",     text: message };
  if (message.includes("✓ ["))        return { agent: "VALIDATOR",    text: message };
  return { agent: "SYSTEM", text: message };
}

// ── Single log entry — memoized ───────────────────────────────────────────────
const LogEntry = memo(function LogEntry({
  log,
  isNew,
}: {
  log: Log;
  isNew: boolean;
}) {
  const { agent, text } = parseLog(log.message);
  const meta = AGENT_META[agent] ?? AGENT_META.SYSTEM;
  const typeColor = TYPE_COLOR[log.action_type] ?? "text-slate-300";
  const ts = new Date(log.timestamp).toLocaleTimeString([], {
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });

  return (
    <motion.div
      initial={isNew ? { opacity: 0, y: 6 } : false}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2 }}
      className={`group flex gap-3 px-4 py-2.5 border-b border-white/[0.03] hover:bg-white/[0.02] transition-colors`}
    >
      {/* Time */}
      <span className="text-[10px] text-slate-600 font-mono shrink-0 mt-0.5 tabular-nums w-[68px]">
        {ts}
      </span>

      {/* Agent badge */}
      <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[9px] font-bold tracking-wider border shrink-0 mt-0.5 h-fit ${meta.color} ${meta.bg} ${meta.border}`}>
        <span className={`w-1 h-1 rounded-full shrink-0 ${meta.dot}`} />
        {agent}
      </span>

      {/* Message */}
      <p className={`flex-1 min-w-0 text-[13px] font-mono leading-relaxed break-words whitespace-pre-wrap ${typeColor}`}>
        {text}
      </p>
    </motion.div>
  );
});

// ── Streaming token entry ─────────────────────────────────────────────────────
const StreamEntry = memo(function StreamEntry({
  nodeId,
  tokens,
}: {
  nodeId: string;
  tokens: string;
}) {
  if (!tokens) return null;
  return (
    <div className="flex gap-3 px-4 py-2.5 border-b border-white/[0.03] bg-cyan-950/10">
      <span className="text-[10px] text-slate-600 font-mono shrink-0 mt-0.5 w-[68px]">
        {new Date().toLocaleTimeString([], { hour12: false })}
      </span>
      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[9px] font-bold tracking-wider border shrink-0 mt-0.5 h-fit text-cyan-400 bg-cyan-950/40 border-cyan-500/30">
        <span className="w-1 h-1 rounded-full bg-cyan-400 animate-pulse" />
        STREAM
      </span>
      <p className="flex-1 min-w-0 text-[13px] font-mono leading-relaxed text-slate-300 break-words whitespace-pre-wrap">
        {tokens}
        <span className="animate-pulse font-bold ml-0.5 text-cyan-400">▍</span>
      </p>
    </div>
  );
});

// ── LogsPanel ─────────────────────────────────────────────────────────────────
export function LogsPanel() {
  const logs = useAppStore((s) => s.logs);
  const status = useAppStore((s) => s.status);
  const streamedTokens = useAppStore((s) => s.streamedTokens);
  const logsPaused = useAppStore((s) => s.logsPaused);
  const setLogsPaused = useAppStore((s) => s.setLogsPaused);
  const selectedNodeId = useAppStore((s) => s.selectedNodeId);

  const scrollRef = useRef<HTMLDivElement>(null);
  const [autoScroll, setAutoScroll] = useState(true);
  const [filterAgent, setFilterAgent] = useState<string>("ALL");
  const prevCount = useRef(0);
  const newLogIds = useRef(new Set<number>());

  // Track newly added logs for enter animation
  useEffect(() => {
    const newOnes = logs.slice(prevCount.current);
    newOnes.forEach((l) => newLogIds.current.add(l.id));
    prevCount.current = logs.length;
  }, [logs]);

  // Auto-scroll
  useEffect(() => {
    if (autoScroll && !logsPaused && scrollRef.current) {
      scrollRef.current.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
    }
  }, [logs, streamedTokens, autoScroll, logsPaused]);

  const handleScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    setAutoScroll(atBottom);
  }, []);

  const scrollToBottom = () => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
    setAutoScroll(true);
  };

  // Filter logs
  const agents = ["ALL", ...Array.from(new Set(logs.map((l) => parseLog(l.message).agent)))];
  const filteredLogs = filterAgent === "ALL"
    ? logs
    : logs.filter((l) => parseLog(l.message).agent === filterAgent);

  const nodeFilteredLogs = selectedNodeId
    ? filteredLogs.filter((l) => l.node_id === selectedNodeId)
    : filteredLogs;

  const isActive = status === "running" || status === "pending";

  return (
    <div className="h-full w-full flex flex-col bg-[#080c14] overflow-hidden font-mono">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-white/5 bg-[#0a0e18] shrink-0">
        <div className="flex items-center gap-2">
          <Terminal className="w-3.5 h-3.5 text-slate-500" />
          <span className="text-[10px] font-mono uppercase tracking-widest text-slate-500">
            Execution Terminal
          </span>
          {isActive && (
            <span className="relative flex h-1.5 w-1.5 ml-1">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-blue-400 opacity-75" />
              <span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-blue-500" />
            </span>
          )}
          <span className="text-[10px] text-slate-600 ml-2 tabular-nums">
            {nodeFilteredLogs.length} entries
          </span>
        </div>

        <div className="flex items-center gap-2">
          {/* Agent filter */}
          <div className="relative">
            <Filter className="w-3 h-3 text-slate-600 absolute left-2 top-1/2 -translate-y-1/2 pointer-events-none" />
            <select
              value={filterAgent}
              onChange={(e) => setFilterAgent(e.target.value)}
              className="h-6 pl-6 pr-2 text-[10px] bg-white/5 border border-white/10 rounded text-slate-400 appearance-none focus:outline-none focus:border-blue-500/50 cursor-pointer"
            >
              {agents.map((a) => (
                <option key={a} value={a} className="bg-[#111827]">{a}</option>
              ))}
            </select>
          </div>

          {/* Pause */}
          <button
            onClick={() => setLogsPaused(!logsPaused)}
            className={`flex items-center gap-1 h-6 px-2 rounded text-[10px] border transition-colors ${
              logsPaused
                ? "bg-amber-500/10 border-amber-500/30 text-amber-400"
                : "bg-white/5 border-white/10 text-slate-500 hover:text-slate-300"
            }`}
          >
            {logsPaused ? <Play className="w-3 h-3" /> : <Pause className="w-3 h-3" />}
            {logsPaused ? "Resume" : "Pause"}
          </button>
        </div>
      </div>

      {/* Selected node indicator */}
      {selectedNodeId && (
        <div className="px-4 py-1.5 bg-blue-950/20 border-b border-blue-500/20 flex items-center gap-2 shrink-0">
          <span className="text-[9px] uppercase tracking-widest text-blue-400 font-mono">
            Filtering: {selectedNodeId}
          </span>
          <button
            onClick={() => useAppStore.getState().setSelectedNodeId(null)}
            className="text-[9px] text-slate-500 hover:text-slate-300 ml-auto"
          >
            ✕ Clear
          </button>
        </div>
      )}

      {/* Log stream */}
      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className="flex-1 overflow-y-auto min-h-0 relative"
        style={{ scrollbarWidth: "thin", scrollbarColor: "#1e293b transparent" }}
      >
        {nodeFilteredLogs.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full gap-3 text-slate-700">
            {isActive ? (
              <span className="text-[12px] font-mono animate-pulse tracking-widest uppercase">
                Waiting for execution…
              </span>
            ) : (
              <span className="text-[12px] font-mono tracking-widest uppercase">
                No logs yet
              </span>
            )}
          </div>
        )}

        {nodeFilteredLogs.map((log) => (
          <LogEntry
            key={log.id}
            log={log}
            isNew={newLogIds.current.has(log.id)}
          />
        ))}

        {Object.entries(streamedTokens).map(([nodeId, tokens]) => (
          <StreamEntry key={`stream-${nodeId}`} nodeId={nodeId} tokens={tokens} />
        ))}
      </div>

      {/* Scroll-to-bottom fab */}
      <AnimatePresence>
        {!autoScroll && (
          <motion.button
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 8 }}
            onClick={scrollToBottom}
            className="absolute bottom-16 right-4 flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-blue-600 text-white text-[11px] font-mono shadow-xl z-20"
          >
            <ChevronDown className="w-3.5 h-3.5" />
            Jump to bottom
          </motion.button>
        )}
      </AnimatePresence>
    </div>
  );
}
