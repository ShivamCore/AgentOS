"use client";

import { useEffect, useRef } from "react";
import { useAppStore } from "@/lib/store";
import { motion } from "framer-motion";

export function SystemMetrics() {
  const resources     = useAppStore((s) => s.resources);
  const status        = useAppStore((s) => s.status);
  const selectedModel = useAppStore((s) => s.selectedModel);
  const taskStartedAt = useAppStore((s) => s.taskStartedAt);
  const fetchResources = useAppStore((s) => s.fetchResources);
  const nodes         = useAppStore((s) => s.nodes);

  useEffect(() => {
    fetchResources();
    const id = setInterval(fetchResources, 5000);
    return () => clearInterval(id);
  }, [fetchResources]);

  const isActive   = status === "running" || status === "pending";
  const queueDepth = resources.queue_pending + resources.queue_running;
  const doneNodes  = nodes.filter((n) => n.status === "completed").length;

  return (
    <div className="w-full shrink-0 border-t border-white/[0.06] bg-[#060a10] h-8 flex items-center px-4 gap-0 overflow-hidden text-[10px] font-mono">

      {/* Status pill */}
      <div className="flex items-center gap-1.5 pr-4 border-r border-white/[0.06] shrink-0">
        <motion.div
          animate={isActive ? { opacity: [0.3, 1, 0.3] } : { opacity: 1 }}
          transition={{ repeat: Infinity, duration: 1.2 }}
          className={`w-1.5 h-1.5 rounded-full shrink-0 ${
            status === "running"   ? "bg-blue-500"
            : status === "completed" ? "bg-emerald-500"
            : status === "failed"    ? "bg-rose-500"
            : status === "pending"   ? "bg-amber-500"
            : "bg-slate-600"
          }`}
        />
        <span className="uppercase tracking-widest text-slate-500">{status}</span>
      </div>

      {/* Metrics — all inline, separated by thin dividers */}
      <div className="flex items-center flex-1 min-w-0 overflow-hidden">

        <Stat
          label="CPU"
          value={`${resources.cpu_percent.toFixed(1)}%`}
          alert={resources.cpu_percent > 80}
          warn={resources.cpu_percent > 50}
        />
        <Sep />
        <Stat
          label="MEM"
          value={`${resources.memory_percent.toFixed(0)}% · ${resources.memory_used_mb.toFixed(0)}MB`}
          alert={resources.memory_percent > 85}
        />
        <Sep />
        <Stat
          label="QUEUE"
          value={queueDepth === 0 ? "idle" : `${queueDepth} (${resources.queue_running} run)`}
          warn={queueDepth > 3}
          alert={queueDepth > 8}
        />
        <Sep />
        <Stat label="DISK"  value={`${resources.disk_used_pct.toFixed(1)}%`} alert={resources.disk_used_pct > 80} />
        <Sep />
        <Stat label="NODES" value={nodes.length > 0 ? `${doneNodes}/${nodes.length}` : "—"} />
        <Sep />
        <Stat label="MODEL" value={selectedModel} color="text-blue-400" />

        {/* Exec timer — only when running */}
        {isActive && taskStartedAt && (
          <>
            <Sep />
            <span className="text-slate-600 px-3 shrink-0">TIME</span>
            <ExecTimer startedAt={taskStartedAt} />
          </>
        )}
      </div>
    </div>
  );
}

// ── Inline stat chip ────────────────────────────────────────────────────────
function Stat({
  label,
  value,
  alert,
  warn,
  color,
}: {
  label: string;
  value: string;
  alert?: boolean;
  warn?: boolean;
  color?: string;
}) {
  const valueColor = color
    ? color
    : alert
    ? "text-rose-400"
    : warn
    ? "text-amber-400"
    : "text-slate-300";

  return (
    <div className="flex items-center gap-1.5 px-3 shrink-0">
      <span className="text-slate-600 uppercase tracking-widest text-[9px]">{label}</span>
      <span className={`tabular-nums ${valueColor}`}>{value}</span>
    </div>
  );
}

// ── Thin separator ────────────────────────────────────────────────────────────
function Sep() {
  return <div className="w-px h-3 bg-white/[0.07] shrink-0" />;
}

// ── Live exec timer via DOM ref (no re-render per tick) ──────────────────────
function ExecTimer({ startedAt }: { startedAt: number }) {
  const ref = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    const tick = () => {
      if (!ref.current) return;
      const s = Math.floor((Date.now() - startedAt) / 1000);
      const m = Math.floor(s / 60);
      ref.current.textContent = m > 0 ? `${m}m ${s % 60}s` : `${s}s`;
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [startedAt]);
  return <span ref={ref} className="text-amber-400 tabular-nums" />;
}
