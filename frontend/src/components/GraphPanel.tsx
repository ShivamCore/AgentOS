"use client";

import { memo } from "react";
import { useAppStore, TaskNode } from "@/lib/store";
import { motion, AnimatePresence } from "framer-motion";
import {
  CheckCircle2,
  CircleDashed,
  Activity,
  XCircle,
  FileCode,
  Zap,
  ArrowDown,
} from "lucide-react";

// ── Connector arrow between nodes ─────────────────────────────────────────────
function Connector({ active }: { active: boolean }) {
  return (
    <div className="flex flex-col items-center py-1 shrink-0">
      <motion.div
        animate={active ? { opacity: [0.4, 1, 0.4] } : { opacity: 0.2 }}
        transition={{ repeat: Infinity, duration: 1.2 }}
        className="w-px h-5 bg-gradient-to-b from-transparent via-slate-500 to-transparent"
      />
      <ArrowDown className={`w-3 h-3 ${active ? "text-blue-400" : "text-slate-700"}`} />
      <motion.div
        animate={active ? { opacity: [0.4, 1, 0.4] } : { opacity: 0.2 }}
        transition={{ repeat: Infinity, duration: 1.2, delay: 0.3 }}
        className="w-px h-5 bg-gradient-to-b from-transparent via-slate-500 to-transparent"
      />
    </div>
  );
}

// ── Single DAG node card ───────────────────────────────────────────────────────
const NodeCard = memo(function NodeCard({
  node,
  index,
  isSelected,
  onClick,
}: {
  node: TaskNode;
  index: number;
  isSelected: boolean;
  onClick: () => void;
}) {
  const isRunning   = node.status === "running";
  const isCompleted = node.status === "completed";
  const isFailed    = node.status === "failed";
  const isPending   = node.status === "pending";

  return (
    <motion.div
      key={node.node_id}
      initial={{ opacity: 0, scale: 0.94, y: 12 }}
      animate={{ opacity: 1, scale: 1, y: 0 }}
      transition={{ delay: index * 0.06, type: "spring", stiffness: 260, damping: 22 }}
      onClick={onClick}
      className={`relative cursor-pointer rounded-xl border p-4 flex flex-col gap-2.5 transition-all duration-300 select-none
        ${isSelected ? "ring-1 ring-blue-500/60 border-blue-500/40 bg-blue-950/20" : ""}
        ${isRunning  && !isSelected ? "bg-blue-950/10 border-blue-500/25 shadow-[0_0_20px_rgba(59,130,246,0.08)]" : ""}
        ${isCompleted && !isSelected ? "bg-emerald-950/5 border-emerald-500/15" : ""}
        ${isFailed   && !isSelected ? "bg-rose-950/10 border-rose-500/25" : ""}
        ${isPending  && !isSelected ? "bg-white/[0.02] border-white/5 opacity-50" : ""}
        ${!isSelected && !isRunning && !isCompleted && !isFailed && !isPending ? "border-white/5 bg-white/[0.02]" : ""}
      `}
    >
      {/* Pulsing outer glow ring when running */}
      {isRunning && (
        <span className="absolute -inset-px rounded-xl border border-blue-500/40 animate-pulse pointer-events-none" />
      )}

      {/* Header row */}
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2.5 min-w-0">
          {isPending   && <CircleDashed className="w-4 h-4 text-slate-600 shrink-0" />}
          {isRunning   && (
            <motion.div animate={{ rotate: 360 }} transition={{ repeat: Infinity, duration: 2, ease: "linear" }}>
              <Activity className="w-4 h-4 text-blue-400 shrink-0" />
            </motion.div>
          )}
          {isCompleted && <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0" />}
          {isFailed    && <XCircle className="w-4 h-4 text-rose-400 shrink-0" />}
          <span className="font-mono text-[11px] uppercase tracking-wider text-slate-400 truncate">
            {node.node_id}
          </span>
        </div>

        <span className={`text-[10px] font-mono uppercase tracking-widest shrink-0 px-2 py-0.5 rounded-full border
          ${isRunning   ? "text-blue-400   bg-blue-950/40   border-blue-500/30"   : ""}
          ${isCompleted ? "text-emerald-400 bg-emerald-950/40 border-emerald-500/30" : ""}
          ${isFailed    ? "text-rose-400   bg-rose-950/40   border-rose-500/30"   : ""}
          ${isPending   ? "text-slate-600  bg-white/5       border-white/10"      : ""}
        `}>
          {node.status}
        </span>
      </div>

      {/* Description */}
      <p className="text-[13px] text-slate-300 leading-relaxed font-sans line-clamp-2">
        {node.description}
      </p>

      {/* Footer */}
      {node.files_modified > 0 && (
        <div className="flex items-center gap-1.5 text-[11px] text-violet-400 font-mono mt-0.5">
          <FileCode className="w-3.5 h-3.5" />
          <span>{node.files_modified} file{node.files_modified !== 1 ? "s" : ""} modified</span>
        </div>
      )}

      {/* Click hint */}
      {!isSelected && (
        <span className="absolute bottom-2 right-3 text-[9px] text-slate-700 uppercase tracking-wider opacity-0 group-hover:opacity-100 transition-opacity">
          click to filter logs
        </span>
      )}
    </motion.div>
  );
});

// ── GraphPanel ────────────────────────────────────────────────────────────────
export function GraphPanel() {
  const nodes = useAppStore((s) => s.nodes);
  const status = useAppStore((s) => s.status);
  const selectedNodeId = useAppStore((s) => s.selectedNodeId);
  const setSelectedNodeId = useAppStore((s) => s.setSelectedNodeId);

  const completedCount = nodes.filter((n) => n.status === "completed").length;
  const progress = nodes.length > 0 ? Math.round((completedCount / nodes.length) * 100) : 0;

  return (
    <div className="h-full flex flex-col bg-[#080c14]">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-white/5 bg-[#0a0e18] shrink-0">
        <div className="flex items-center gap-2">
          <Zap className="w-3.5 h-3.5 text-slate-500" />
          <span className="text-[10px] font-mono uppercase tracking-widest text-slate-500">
            DAG Execution Flow
          </span>
          {nodes.length > 0 && (
            <span className="text-[10px] text-slate-600 ml-2 font-mono">
              {completedCount}/{nodes.length} nodes
            </span>
          )}
        </div>

        {/* Progress bar */}
        {nodes.length > 0 && (
          <div className="flex items-center gap-2">
            <div className="w-24 h-1 rounded-full bg-white/5 overflow-hidden">
              <motion.div
                className="h-full rounded-full bg-gradient-to-r from-blue-500 to-violet-500"
                initial={{ width: 0 }}
                animate={{ width: `${progress}%` }}
                transition={{ type: "spring", stiffness: 60, damping: 15 }}
              />
            </div>
            <span className="text-[10px] font-mono tabular-nums text-slate-500">{progress}%</span>
          </div>
        )}
      </div>

      {/* Graph area */}
      <div
        className="flex-1 overflow-y-auto min-h-0 p-5 flex flex-col items-center"
        style={{ scrollbarWidth: "thin", scrollbarColor: "#1e293b transparent" }}
      >
        {nodes.length === 0 ? (
          <div className="w-full h-full flex flex-col items-center justify-center gap-3 text-slate-700">
            <div className="w-10 h-10 rounded-xl border border-white/5 bg-white/[0.02] flex items-center justify-center">
              <Zap className="w-5 h-5 text-slate-700" />
            </div>
            <span className="text-[11px] font-mono uppercase tracking-widest">
              {status === "pending" || status === "running" ? "Building graph…" : "Graph will appear here"}
            </span>
          </div>
        ) : (
          <div className="flex flex-col w-full max-w-md gap-0">
            <AnimatePresence>
              {nodes.map((node, index) => (
                <div key={node.node_id} className="flex flex-col items-center w-full">
                  <div className="w-full group">
                    <NodeCard
                      node={node}
                      index={index}
                      isSelected={selectedNodeId === node.node_id}
                      onClick={() =>
                        setSelectedNodeId(
                          selectedNodeId === node.node_id ? null : node.node_id
                        )
                      }
                    />
                  </div>
                  {index < nodes.length - 1 && (
                    <Connector active={node.status === "running" || nodes[index + 1].status === "running"} />
                  )}
                </div>
              ))}
            </AnimatePresence>
          </div>
        )}
      </div>
    </div>
  );
}
