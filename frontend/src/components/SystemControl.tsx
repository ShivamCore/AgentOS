import { useState, useEffect } from "react";
import { api } from "@/lib/api";
import { motion } from "framer-motion";

export function SystemControl() {
  const [status, setStatus] = useState<"running" | "stopped" | "checking">("checking");
  const [isProcessing, setIsProcessing] = useState(false);
  const [processMessage, setProcessMessage] = useState("");
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    const fetchStatus = async () => {
      if (isProcessing) return;
      try {
        const resp = await api.getSystemStatus();
        if (mounted) setStatus(resp.status);
      } catch {
        if (mounted) {
          setStatus("stopped");
        }
      }
    };

    fetchStatus();
    const interval = setInterval(fetchStatus, 3000);
    return () => {
      mounted = false;
      clearInterval(interval);
    };
  }, [isProcessing]);

  const handleStart = async () => {
    setIsProcessing(true);
    setProcessMessage("Starting backend system...");
    setErrorMsg(null);
    
    try {
      await api.startSystem();
      setStatus("running");
    } catch {
      setErrorMsg("System failed to start");
    } finally {
      setIsProcessing(false);
    }
  };

  const handleStop = async () => {
    setIsProcessing(true);
    setProcessMessage("Stopping backend system...");
    try {
      await api.stopSystem();
      setStatus("stopped");
    } catch {
      setErrorMsg("System failed to stop");
    } finally {
      setIsProcessing(false);
    }
  };

  return (
    <div className="flex items-center gap-4 bg-[#111827] px-4 py-1.5 rounded-full border border-white/5 shadow-inner shrink-0">
      
      {/* Status Output Loop */}
      <div className="flex flex-col items-center justify-center min-w-[130px]">
        {isProcessing ? (
          <motion.span 
            initial={{ opacity: 0 }} 
            animate={{ opacity: 1 }} 
            className="text-[9px] text-blue-400 opacity-80 animate-pulse"
          >
            {processMessage}
          </motion.span>
        ) : errorMsg ? (
          <span className="text-[9px] text-red-500 opacity-80">{errorMsg}</span>
        ) : (
          <div className="flex items-center gap-2">
            {status === "running" ? (
              <motion.div animate={{ opacity: [0.3, 1, 0.3] }} transition={{ repeat: Infinity, duration: 1 }}>
                <div className="w-2 h-2 rounded-full bg-green-500 shadow-[0_0_8px_rgba(34,197,94,0.8)]" />
              </motion.div>
            ) : status === "stopped" ? (
              <div className="w-2 h-2 rounded-full bg-red-500 shadow-[0_0_8px_rgba(239,68,68,0.5)]" />
            ) : (
              <div className="w-2 h-2 rounded-full bg-gray-600" />
            )}
            <span className={`text-[10px] font-semibold ${status === "running" ? "text-green-500" : status === "stopped" ? "text-red-500" : "text-gray-500"}`}>
              SYSTEM: {status.toUpperCase()}
            </span>
          </div>
        )}
      </div>

      {/* Control Buttons */}
      <div className="flex items-center gap-2 border-l border-white/10 pl-4">
        {status !== "running" && (
          <button 
            onClick={handleStart} 
            disabled={isProcessing}
            className="px-3 py-1 rounded text-[10px] font-semibold bg-blue-500/20 text-blue-400 hover:bg-blue-500/30 hover:shadow-[0_0_15px_rgba(59,130,246,0.3)] transition-all disabled:opacity-50 disabled:cursor-not-allowed"
          >
            START
          </button>
        )}
        
        {status === "running" && (
          <button 
            onClick={handleStop}
            disabled={isProcessing}
            className="px-3 py-1 rounded text-[10px] font-semibold bg-red-500/10 text-red-400 hover:bg-red-500/20 transition-all disabled:opacity-50 disabled:cursor-not-allowed"
          >
            STOP
          </button>
        )}
      </div>

    </div>
  );
}
