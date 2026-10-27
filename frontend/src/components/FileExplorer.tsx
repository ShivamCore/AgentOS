"use client";

import { useMemo, useState } from "react";
import { useAppStore, FileEdit } from "@/lib/store";
import { motion, AnimatePresence } from "framer-motion";
import { FolderOpen, File, ChevronRight, Sparkles } from "lucide-react";

// ── Build a tree from flat FileEdit list ─────────────────────────────────────
type TreeNode = {
  name: string;
  path: string;
  isDir: boolean;
  children: TreeNode[];
  edit?: FileEdit;
  isNew?: boolean;
};

function buildTree(edits: FileEdit[]): TreeNode[] {
  const root: TreeNode[] = [];
  const map = new Map<string, TreeNode>();

  // Sort by created_at so newest wins
  const sorted = [...edits].sort(
    (a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime()
  );
  const latestPaths = new Set(
    sorted.slice(-Math.min(3, sorted.length)).map((e) => e.file_path)
  );

  edits.forEach((edit) => {
    const parts = edit.file_path.split("/").filter(Boolean);
    let currentChildren = root;
    let fullPath = "";

    parts.forEach((part, i) => {
      fullPath = fullPath ? `${fullPath}/${part}` : part;
      const existing = map.get(fullPath);
      if (!existing) {
        const isLeaf = i === parts.length - 1;
        const node: TreeNode = {
          name: part,
          path: fullPath,
          isDir: !isLeaf,
          children: [],
          edit: isLeaf ? edit : undefined,
          isNew: isLeaf && latestPaths.has(edit.file_path),
        };
        map.set(fullPath, node);
        currentChildren.push(node);
        currentChildren = node.children;
      } else {
        currentChildren = existing.children;
      }
    });
  });

  return root;
}

// ── Language detection ────────────────────────────────────────────────────────
function langFromPath(path: string): string {
  const ext = path.split(".").pop() ?? "";
  const map: Record<string, string> = {
    ts: "typescript", tsx: "tsx", js: "javascript", jsx: "jsx",
    py: "python", sh: "bash", json: "json", md: "markdown",
    yml: "yaml", yaml: "yaml", css: "css", html: "html",
    toml: "toml", rs: "rust", go: "go",
  };
  return map[ext] ?? "text";
}

// ── Tree node row ─────────────────────────────────────────────────────────────
function TreeRow({
  node,
  depth,
  selected,
  onSelect,
}: {
  node: TreeNode;
  depth: number;
  selected: string | null;
  onSelect: (n: TreeNode) => void;
}) {
  const [open, setOpen] = useState(true);
  const isSelected = selected === node.path;

  return (
    <>
      <div
        className={`flex items-center gap-1.5 py-1 px-3 rounded cursor-pointer text-[12px] font-mono transition-colors group
          ${isSelected ? "bg-blue-950/40 text-blue-300" : "text-slate-400 hover:bg-white/[0.03] hover:text-slate-200"}`}
        style={{ paddingLeft: `${12 + depth * 14}px` }}
        onClick={() => {
          if (node.isDir) setOpen((o) => !o);
          else onSelect(node);
        }}
      >
        {node.isDir ? (
          <>
            <ChevronRight
              className={`w-3 h-3 text-slate-600 shrink-0 transition-transform duration-200 ${open ? "rotate-90" : ""}`}
            />
            <FolderOpen className="w-3.5 h-3.5 text-amber-500/70 shrink-0" />
          </>
        ) : (
          <>
            <span className="w-3 h-3 shrink-0" />
            <File className={`w-3.5 h-3.5 shrink-0 ${node.isNew ? "text-emerald-400" : "text-slate-500"}`} />
          </>
        )}
        <span className="truncate flex-1 min-w-0">{node.name}</span>
        {node.isNew && (
          <span className="shrink-0">
            <Sparkles className="w-2.5 h-2.5 text-emerald-400" />
          </span>
        )}
      </div>
      {node.isDir && open && node.children.map((child) => (
        <TreeRow
          key={child.path}
          node={child}
          depth={depth + 1}
          selected={selected}
          onSelect={onSelect}
        />
      ))}
    </>
  );
}

// ── FileExplorer (left side of diff panel) ────────────────────────────────────
export function FileExplorer({
  selected,
  onSelect,
}: {
  selected: string | null;
  onSelect: (path: string, content: string) => void;
}) {
  const fileEdits = useAppStore((s) => s.fileEdits);
  const tree = useMemo(() => buildTree(fileEdits), [fileEdits]);

  const handleSelect = (node: TreeNode) => {
    if (node.edit) {
      onSelect(node.path, node.edit.content);
    }
  };

  if (fileEdits.length === 0) {
    return (
      <div className="w-full h-full flex flex-col items-center justify-center gap-2 text-slate-700">
        <FolderOpen className="w-6 h-6 text-slate-800" />
        <span className="text-[11px] font-mono uppercase tracking-widest">No files yet</span>
      </div>
    );
  }

  return (
    <div
      className="w-full h-full overflow-y-auto py-2"
      style={{ scrollbarWidth: "thin", scrollbarColor: "#1e293b transparent" }}
    >
      <AnimatePresence>
        {tree.map((node) => (
          <motion.div
            key={node.path}
            initial={{ opacity: 0, x: -6 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ duration: 0.2 }}
          >
            <TreeRow node={node} depth={0} selected={selected} onSelect={handleSelect} />
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  );
}
