"use client";

import { motion } from "framer-motion";
import { useAppStore, TaskHistory } from "@/lib/store";
import { CheckCircle2, XCircle, Clock, Play, RotateCcw } from "lucide-react";
import { memo } from "react";

const STATUS_META = {
  completed: { icon: CheckCircle2, color: "text-emerald-400", bg: "bg-emerald-950/20 border-emerald-500/15", dot: "bg-emerald-400" },
  failed:    { icon: XCircle,      color: "text-rose-400",    bg: "bg-rose-950/20 border-rose-500/15",       dot: "bg-rose-400"    },
  running:   { icon: Play,         color: "text-blue-400",    bg: "bg-blue-950/20 border-blue-500/20",       dot: "bg-blue-400"    },
  pending:   { icon: Clock,        color: "text-amber-400",   bg: "bg-amber-950/20 border-amber-500/15",     dot: "bg-amber-400"   },
  idle:      { icon: Clock,        color: "text-slate-500",   bg: "bg-white/[0.02] border-white/5",          dot: "bg-slate-600"   },
};

function timeAgo(iso: string): string {
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 60)  return `${Math.floor(diff)}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  return `${Math.floor(diff / 3600)}h ago`;
}

const HistoryItem = memo(function HistoryItem({
  task,
  index,
  isCurrent,
  onReplay,
}: {
  task: TaskHistory;
  index: number;
  isCurrent: boolean;
  onReplay: (t: TaskHistory) => void;
}) {
  const meta = STATUS_META[task.status] ?? STATUS_META.idle;
  const Icon = meta.icon;

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: index * 0.04 }}
      className={`group relative flex flex-col gap-2 p-3 rounded-xl border cursor-pointer transition-colors
        ${isCurrent ? "ring-1 ring-blue-500/40 " : ""}
        ${meta.bg}`}
      onClick={() => onReplay(task)}
    >
      {/* Header */}
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${meta.dot} ${task.status === "running" ? "animate-pulse" : ""}`} />
          <span className={`text-[9px] font-mono uppercase tracking-wider shrink-0 ${meta.color}`}>
            {task.status}
          </span>
        </div>
        <span className="text-[9px] text-slate-600 font-mono shrink-0">
          {timeAgo(task.created_at)}
        </span>
      </div>

      {/* Description */}
      <p className="text-[12px] text-slate-300 leading-snug line-clamp-2 font-sans">
        {task.description}
      </p>

      {/* Task ID */}
      <span className="text-[9px] text-slate-700 font-mono truncate">
        {task.id.split("-")[0]}…
      </span>

      {/* Replay hint */}
      <div className="absolute top-2.5 right-2.5 opacity-0 group-hover:opacity-100 transition-opacity">
        <RotateCcw className="w-3.5 h-3.5 text-slate-500" />
      </div>
    </motion.div>
  );
});

export function CommandHistoryPanel() {
  const taskHistory = useAppStore((s) => s.taskHistory);
  const taskId = useAppStore((s) => s.taskId);
  const startTask = useAppStore((s) => s.startTask);
  const status = useAppStore((s) => s.status);

  // Sorted newest first
  const sorted = [...taskHistory].sort(
    (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
  );

  const handleReplay = (t: TaskHistory) => {
    if (status === "running" || status === "pending") return;
    startTask(t.description);
  };

  return (
    <div className="h-full flex flex-col bg-[#080c14]">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-white/5 bg-[#0a0e18] shrink-0">
        <span className="text-[10px] font-mono uppercase tracking-widest text-slate-500">
          Command History
        </span>
        <span className="text-[10px] tabular-nums text-slate-600 font-mono">
          {taskHistory.length} tasks
        </span>
      </div>

      {/* List */}
      <div
        className="flex-1 overflow-y-auto min-h-0 p-3 flex flex-col gap-2"
        style={{ scrollbarWidth: "thin", scrollbarColor: "#1e293b transparent" }}
      >
        {sorted.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full gap-2 text-slate-700">
            <Clock className="w-5 h-5 text-slate-800" />
            <span className="text-[11px] font-mono uppercase tracking-widest">No history yet</span>
          </div>
        ) : (
          sorted.map((task, i) => (
            <HistoryItem
              key={task.id}
              task={task}
              index={i}
              isCurrent={task.id === taskId}
              onReplay={handleReplay}
            />
          ))
        )}
      </div>
    </div>
  );
}
