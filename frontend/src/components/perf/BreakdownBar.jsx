import React from 'react';
import { Tooltip } from 'antd';
import { waitClassColor, waitClassLabel, WAIT_CLASS_ORDER } from './waitClassMeta';

/** 行内按等待类分段堆叠条 (Top SQL/Top 会话表格用, EMCC 形制) */
export default function BreakdownBar({ breakdown = {}, pct = 100 }) {
  const total = Object.values(breakdown).reduce((a, b) => a + b, 0);
  if (!total) return <div style={{ height: 14 }} />;
  const tip = WAIT_CLASS_ORDER.filter((k) => breakdown[k])
    .map((k) => `${waitClassLabel(k)}: ${breakdown[k].toFixed(1)}s`).join(' | ');
  return (
    <Tooltip title={tip}>
      <div style={{ display: 'flex', width: `${Math.max(pct, 2)}%`, minWidth: 24,
                    height: 14, borderRadius: 2, overflow: 'hidden' }}>
        {WAIT_CLASS_ORDER.filter((k) => breakdown[k]).map((k) => (
          <div key={k} style={{ width: `${(breakdown[k] / total) * 100}%`,
                                background: waitClassColor(k) }} />
        ))}
      </div>
    </Tooltip>
  );
}
