import React, { useEffect, useState } from 'react';
import ReactFlow, { Background, Controls, Edge, Node } from 'reactflow';
import 'reactflow/dist/style.css';

interface ExecutionLog {
  type: string;
  content: string;
  time: string;
}

interface StepNode {
  step_id: string;
  description: string;
  status: 'CREATED' | 'RUNNING' | 'COMPLETED' | 'FAILED';
  execution_logs: ExecutionLog[];
}

interface ExecutionGraphProps {
  taskId: string;
}

export const ExecutionGraph: React.FC<ExecutionGraphProps> = ({ taskId }) => {
  const [nodes, setNodes] = useState<Node[]>([]);
  const [edges, setEdges] = useState<Edge[]>([]);
  const [selectedStep, setSelectedStep] = useState<StepNode | null>(null);

  useEffect(() => {
    // Poll REST API (or ingest WebSocket) tracing execution boundaries
    const fetchDAG = async () => {
      try {
        const response = await fetch(`/api/tasks/${taskId}/steps`);
        const steps: StepNode[] = await response.json();

        // ── MAP TO REACT FLOW UI NODES
        const flowNodes = steps.map((step, index) => ({
          id: step.step_id,
          position: { x: 250 * index, y: index % 2 === 0 ? 100 : 250 },
          data: { label: `${step.step_id}: ${step.status}` },
          style: {
            background: step.status === 'COMPLETED' ? '#4CAF50' : step.status === 'FAILED' ? '#F44336' : step.status === 'RUNNING' ? '#2196F3' : '#9E9E9E',
            color: '#fff',
            borderRadius: '8px',
            padding: '10px'
          }
        }));

        // Note: Edge construction should map strictly bounding \`step.dependencies\` dynamically.
        // Simplified pseudo-edge mapper for visual demonstration:
        const flowEdges = steps.map((step, idx) => {
          if (idx === 0) return null;
          return { id: `e-${steps[idx-1].step_id}-${step.step_id}`, source: steps[idx-1].step_id, target: step.step_id, animated: step.status === 'RUNNING' };
        }).filter(Boolean) as Edge[];

        setNodes(flowNodes);
        setEdges(flowEdges);
      } catch (e) {
        console.error("Failed to map DAG status UI:", e);
      }
    };

    const interval = setInterval(fetchDAG, 2000);
    fetchDAG();
    return () => clearInterval(interval);
  }, [taskId]);

  return (
    <div style={{ display: 'flex', height: '100%', width: '100%' }}>
      {/* Visual Execution Graph Panel */}
      <div style={{ flex: 2, borderRight: '1px solid #ccc' }}>
        <ReactFlow nodes={nodes} edges={edges} onNodeClick={(_, node) => {
             fetch(`/api/tasks/${taskId}/steps`)
               .then(res => res.json())
               .then(data => setSelectedStep(data.find((s: any) => s.step_id === node.id)));
        }}>
          <Background />
          <Controls />
        </ReactFlow>
      </div>

      {/* Trace Observability Panel (Item 6 & 7) */}
      {selectedStep && (
        <div style={{ flex: 1, padding: '20px', overflowY: 'auto', background: '#fafafa' }}>
          <h3>Step Scope: {selectedStep.step_id}</h3>
          <p><strong>Status:</strong> {selectedStep.status}</p>
          <p><strong>Goal:</strong> {selectedStep.description}</p>
          <hr/>
          <h4>Native Execution Tracing Logs</h4>
          <pre style={{ background: '#1e1e1e', color: '#d4d4d4', padding: '10px', borderRadius: '4px', fontSize: '11px' }}>
            {selectedStep.execution_logs.map((log, i) => (
              <div key={i}>
                <span style={{ color: '#569cd6' }}>[{log.time}]</span> 
                <span style={{ color: log.type === 'error' ? '#f44747' : '#ce9178' }}> [{log.type.toUpperCase()}] </span>
                {log.content}
              </div>
            ))}
          </pre>
        </div>
      )}
    </div>
  );
};
