"use client";

import { useEffect, useState } from "react";
import { CommandBar } from "@/components/CommandBar";
import { LogsPanel } from "@/components/LogsPanel";
import { GraphPanel } from "@/components/GraphPanel";
import { DiffViewer } from "@/components/DiffViewer";
import { CommandHistoryPanel } from "@/components/CommandHistoryPanel";
import { ExecutionTimeline } from "@/components/ExecutionTimeline";
import { SystemMetrics } from "@/components/SystemMetrics";
import { TerminalPanel } from "@/components/TerminalPanel";
import { AgentEditor } from "@/components/AgentEditor";
import { useAppStore } from "@/lib/store";
import { motion, AnimatePresence } from "framer-motion";
import { Terminal, Clock, History, Layout, GitBranch, Files, TerminalSquare } from "lucide-react";

// ── Tab config ────────────────────────────────────────────────────────────────
const LEFT_TABS = [
  { id: "logs"     as const, label: "Terminal", icon: Terminal, accent: "blue"    },
  { id: "timeline" as const, label: "Timeline", icon: Clock,    accent: "violet"  },
  { id: "history"  as const, label: "History",  icon: History,  accent: "emerald" },
  { id: "agents"   as const, label: "Agents",   icon: Terminal, accent: "blue"    },
];

const RIGHT_TABS = [
  { id: "graph"    as const, label: "DAG Graph",  icon: GitBranch,    accent: "blue"    },
  { id: "files"    as const, label: "File Diffs", icon: Files,         accent: "violet"  },
  { id: "terminal" as const, label: "Terminal",   icon: TerminalSquare, accent: "emerald" },
];

const ACCENT: Record<string, { active: string; text: string }> = {
  blue:    { active: "bg-blue-950/30 border-blue-500/30",    text: "text-blue-400"    },
  violet:  { active: "bg-violet-950/30 border-violet-500/30",text: "text-violet-400"  },
  emerald: { active: "bg-emerald-950/30 border-emerald-500/30",text: "text-emerald-400"},
};

export default function Home() {
  const taskId       = useAppStore((s) => s.taskId);
  const status       = useAppStore((s) => s.status);
  const activeTab    = useAppStore((s) => s.activeTab);
  const setActiveTab = useAppStore((s) => s.setActiveTab);
  const fetchModels  = useAppStore((s) => s.fetchModels);
  const pollState    = useAppStore((s) => s.pollState);

  const [rightTab, setRightTab] = useState<"graph" | "files" | "terminal">("graph");

  useEffect(() => {
    fetchModels();
    pollState();
  }, [fetchModels, pollState]);

  const hasTask = status !== "idle" || taskId !== null;

  return (
    <div
      className="flex flex-col w-screen bg-[#06090f] overflow-hidden text-slate-200 antialiased"
      style={{ height: "100dvh" }}
    >

      {/* ── AMBIENT BG ─────────────────────────────────────────────── */}
      <div className="pointer-events-none fixed inset-0 z-0">
        <div className="absolute inset-0 bg-[radial-gradient(ellipse_80%_50%_at_50%_-20%,rgba(59,130,246,0.06),transparent)]" />
        <div className="absolute inset-0 bg-[radial-gradient(ellipse_60%_40%_at_80%_80%,rgba(139,92,246,0.04),transparent)]" />
        <div
          className="absolute inset-0 opacity-[0.015]"
          style={{
            backgroundImage:
              "linear-gradient(rgba(255,255,255,0.1) 1px, transparent 1px)," +
              "linear-gradient(90deg, rgba(255,255,255,0.1) 1px, transparent 1px)",
            backgroundSize: "40px 40px",
          }}
        />
      </div>

      {/* ── COMMAND BAR ─────────────────────────────────────────────── */}
      <CommandBar />

      {/* ── MAIN WORKSPACE ──────────────────────────────────────────── */}
      <div className="flex flex-1 min-h-0 w-full relative z-10 overflow-hidden">

        <AnimatePresence mode="wait">
          {hasTask ? (
            <motion.div
              key="workspace"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.2 }}
              className="flex flex-1 min-w-0 h-full overflow-hidden"
            >
              {/* ───────────────────── LEFT: Logs panel ───────────────────── */}
              {/* Fixed at 55% of viewport width — gives logs room to breathe */}
              <div className="flex flex-col h-full border-r border-white/[0.06]"
                   style={{ width: "55%", minWidth: 0 }}>

                {/* Tab strip */}
                <div className="flex items-center gap-0.5 px-3 pt-2 pb-1.5 border-b border-white/[0.05] bg-[#080c14] shrink-0">
                  {LEFT_TABS.map(({ id, label, icon: Icon, accent }) => {
                    const isActive = activeTab === id;
                    const a = ACCENT[accent];
                    return (
                      <button
                        key={id}
                        onClick={() => setActiveTab(id)}
                        className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[10px] font-mono
                          uppercase tracking-widest border transition-all duration-200
                          ${isActive
                            ? `${a.active} ${a.text} border-current`
                            : "border-transparent text-slate-600 hover:text-slate-400 hover:bg-white/[0.03]"
                          }`}
                      >
                        <Icon className="w-3 h-3" />
                        {label}
                      </button>
                    );
                  })}
                </div>

                {/* Tab content — all mounted, opacity-switched for zero layout shift */}
                <div className="flex-1 relative min-h-0 overflow-hidden">
                  {LEFT_TABS.map(({ id }) => (
                    <div
                      key={id}
                      className={`absolute inset-0 transition-opacity duration-200
                        ${activeTab === id
                          ? "opacity-100 pointer-events-auto z-10"
                          : "opacity-0 pointer-events-none z-0"}`}
                    >
                      {id === "logs"     && <LogsPanel />}
                      {id === "timeline" && <ExecutionTimeline />}
                      {id === "history"  && <CommandHistoryPanel />}
                      {id === "agents"   && <AgentEditor />}
                    </div>
                  ))}
                </div>
              </div>

              {/* ───────────────────── RIGHT: Graph / Files ───────────────── */}
              <div className="flex flex-col flex-1 min-w-0 min-h-0 h-full overflow-hidden">

                {/* Tab strip */}
                <div className="flex items-center gap-0.5 px-3 pt-2 pb-1.5 border-b border-white/[0.05] bg-[#080c14] shrink-0">
                  {RIGHT_TABS.map(({ id, label, icon: Icon, accent }) => {
                    const isActive = rightTab === id;
                    const a = ACCENT[accent];
                    return (
                      <button
                        key={id}
                        onClick={() => setRightTab(id as "graph" | "files" | "terminal")}
                        className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[10px] font-mono
                          uppercase tracking-widest border transition-all duration-200
                          ${isActive
                            ? `${a.active} ${a.text} border-current`
                            : "border-transparent text-slate-600 hover:text-slate-400 hover:bg-white/[0.03]"
                          }`}
                      >
                        <Icon className="w-3 h-3" />
                        {label}
                      </button>
                    );
                  })}
                </div>

                {/* Tab content */}
                <div className="flex-1 relative min-h-0 overflow-hidden">
                  <div className={`absolute inset-0 transition-opacity duration-200
                    ${rightTab === "graph" ? "opacity-100 pointer-events-auto z-10" : "opacity-0 pointer-events-none z-0"}`}>
                    <GraphPanel />
                  </div>
                  <div className={`absolute inset-0 transition-opacity duration-200
                    ${rightTab === "files" ? "opacity-100 pointer-events-auto z-10" : "opacity-0 pointer-events-none z-0"}`}>
                    <DiffViewer />
                  </div>
                  <div className={`absolute inset-0 transition-opacity duration-200
                    ${rightTab === "terminal" ? "opacity-100 pointer-events-auto z-10" : "opacity-0 pointer-events-none z-0"}`}>
                    <TerminalPanel />
                  </div>
                </div>
              </div>
            </motion.div>

          ) : (
            /* ── EMPTY STATE ─────────────────────────────────────────── */
            <motion.div
              key="empty"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.3 }}
              className="absolute inset-0 flex flex-col items-center justify-center gap-8 z-10"
            >
              <motion.div
                animate={{ scale: [1, 1.08, 1], opacity: [0.3, 0.6, 0.3] }}
                transition={{ repeat: Infinity, duration: 3.5, ease: "easeInOut" }}
                className="w-24 h-24 rounded-full bg-gradient-to-br from-blue-500/20 to-violet-500/20 blur-2xl"
              />

              <div className="flex flex-col items-center gap-4 -mt-20">
                <div className="w-14 h-14 rounded-2xl bg-gradient-to-br from-blue-500/10 to-violet-500/10 border border-blue-500/20 flex items-center justify-center shadow-2xl">
                  <Layout className="w-7 h-7 text-blue-400" />
                </div>
                <div className="flex flex-col items-center gap-2 text-center">
                  <h1 className="text-xl font-semibold tracking-[0.15em] uppercase text-white">
                    AgentOS
                  </h1>
                  <p className="text-[12px] font-mono text-slate-600 tracking-widest uppercase">
                    AI Developer Operating System
                  </p>
                  <div className="flex items-center gap-1 text-[11px] font-mono text-slate-700 mt-2">
                    <span>Type a task and press</span>
                    <kbd className="px-1.5 py-0.5 rounded border border-white/10 bg-white/[0.03] text-slate-500 text-[10px]">Enter</kbd>
                    <span>to begin</span>
                  </div>
                </div>
              </div>

              <div className="flex items-center gap-3 flex-wrap justify-center max-w-lg px-6">
                {["Real-time streaming", "DAG execution", "Live file tracking", "Multi-agent", "Diff view"].map((cap) => (
                  <span key={cap} className="px-2.5 py-1 rounded-full border border-white/[0.06] bg-white/[0.02] text-[10px] font-mono text-slate-600 uppercase tracking-wider">
                    {cap}
                  </span>
                ))}
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      {/* ── SYSTEM METRICS BAR ──────────────────────────────────────── */}
      <SystemMetrics />
    </div>
  );
}
