import React, { useState } from 'react';

export const TaskBuilder: React.FC = () => {
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [taskType, setTaskType] = useState('build_app');
  const [techStack, setTechStack] = useState('');
  const [features, setFeatures] = useState('');
  
  // Constraints mappings
  const [maxTime, setMaxTime] = useState(300);
  const [maxSteps, setMaxSteps] = useState(15);
  const [riskLevel, setRiskLevel] = useState('balanced');
  const [fileScope, setFileScope] = useState('');
  
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<any>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);

    const payload = {
      title,
      description,
      task_type: taskType,
      tech_stack: techStack.split(',').map(s => s.trim()).filter(Boolean),
      features: features.split(',').map(s => s.trim()).filter(Boolean),
      constraints: {
        max_time: Number(maxTime),
        max_steps: Number(maxSteps),
        risk_level: riskLevel,
        file_scope: fileScope.split(',').map(s => s.trim()).filter(Boolean)
      }
    };

    try {
      const res = await fetch('http://localhost:8000/tasks/create', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      setResult(data);
    } catch (err) {
      console.error(err);
    }
    setLoading(false);
  };

  return (
    <div style={{ padding: '20px', maxWidth: '800px', margin: '0 auto', fontFamily: 'system-ui' }}>
      <h2 style={{ fontSize: '24px', marginBottom: '20px' }}>Task Builder Studio</h2>
      <form onSubmit={handleSubmit} style={{ display: 'grid', gap: '20px', background: '#f5f5f5', padding: '30px', borderRadius: '8px' }}>
        
        {/* Core Input Abstraction */}
        <div>
          <label style={{ display: 'block', fontWeight: 'bold', marginBottom: '5px' }}>Task Title</label>
          <input required type="text" value={title} onChange={(e) => setTitle(e.target.value)} style={{ width: '100%', padding: '10px' }} placeholder="E.g., Authentication Provider implementation" />
        </div>
        
        <div>
          <label style={{ display: 'block', fontWeight: 'bold', marginBottom: '5px' }}>Description Focus</label>
          <textarea required value={description} onChange={(e) => setDescription(e.target.value)} style={{ width: '100%', padding: '10px', height: '100px' }} placeholder="Explain specific goal behavior..."></textarea>
        </div>

        <div style={{ display: 'flex', gap: '20px' }}>
          <div style={{ flex: 1 }}>
            <label style={{ display: 'block', fontWeight: 'bold', marginBottom: '5px' }}>Task Type Classification</label>
            <select value={taskType} onChange={(e) => setTaskType(e.target.value)} style={{ width: '100%', padding: '10px' }}>
              <option value="build_app">Build App</option>
              <option value="fix_bug">Fix Bug</option>
              <option value="refactor_code">Refactor Code</option>
              <option value="create_api">Create API</option>
            </select>
          </div>
          
          <div style={{ flex: 1 }}>
            <label style={{ display: 'block', fontWeight: 'bold', marginBottom: '5px' }}>Tech Stack (csv)</label>
            <input type="text" value={techStack} onChange={(e) => setTechStack(e.target.value)} style={{ width: '100%', padding: '10px' }} placeholder="React, Node, FastApi" />
          </div>
        </div>

        <div>
          <label style={{ display: 'block', fontWeight: 'bold', marginBottom: '5px' }}>Feature Checklist (csv)</label>
          <input type="text" value={features} onChange={(e) => setFeatures(e.target.value)} style={{ width: '100%', padding: '10px' }} placeholder="OAuth2, Role Based Access, Token Rotation" />
        </div>

        {/* Constraint Engine Hookins */}
        <hr style={{ borderTop: '1px solid #ccc', margin: '15px 0' }} />
        <h3 style={{ fontSize: '18px', marginBottom: '10px' }}>Constraint Engine Limits</h3>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '15px' }}>
          <div>
            <label style={{ display: 'block', fontSize: '12px' }}>Max Timeout (sec)</label>
            <input type="number" value={maxTime} onChange={(e) => setMaxTime(Number(e.target.value))} style={{ width: '100%', padding: '8px' }} />
          </div>
          <div>
            <label style={{ display: 'block', fontSize: '12px' }}>Max Steps (DAG nodes)</label>
            <input type="number" value={maxSteps} onChange={(e) => setMaxSteps(Number(e.target.value))} style={{ width: '100%', padding: '8px' }} />
          </div>
          <div>
            <label style={{ display: 'block', fontSize: '12px' }}>Risk Level Bound</label>
            <select value={riskLevel} onChange={(e) => setRiskLevel(e.target.value)} style={{ width: '100%', padding: '8px' }}>
              <option value="safe">Safe (Conservative)</option>
              <option value="balanced">Balanced</option>
              <option value="aggressive">Aggressive</option>
            </select>
          </div>
        </div>
        
        <div>
          <label style={{ display: 'block', fontWeight: 'bold', marginBottom: '5px', fontSize: '12px' }}>Restrict File Scopes (csv dirs)</label>
          <input type="text" value={fileScope} onChange={(e) => setFileScope(e.target.value)} style={{ width: '100%', padding: '10px' }} placeholder="src/components, backend/api" />
        </div>

        <button disabled={loading} type="submit" style={{ padding: '15px', background: '#2563eb', color: '#fff', fontSize: '16px', fontWeight: 'bold', border: 'none', borderRadius: '4px', cursor: 'pointer' }}>
          {loading ? 'Booting Execution Pipeline...' : 'Initialize Isolated DAG Agent Task'}
        </button>
      </form>

      {result && (
        <div style={{ marginTop: '20px', padding: '15px', background: '#dcfce7', color: '#166534', borderRadius: '5px' }}>
          <strong>Task Successfully Dispatched Natively:</strong> {result.task_id}
        </div>
      )}
    </div>
  );
};
