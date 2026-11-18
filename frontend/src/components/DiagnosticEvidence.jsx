import { Alert, Space, Tag, Typography } from 'antd'
import { Link } from 'react-router-dom'
import { operationTime } from '../utils/operations'

function EvidenceAnchor({ entry }) {
  const focus = entry.focus
  if (!focus || !/^(incident-(events|changes|metrics|evidence)|event-[a-zA-Z0-9_-]+)$/.test(focus)) return null
  return <a href={`#${focus}`} onClick={event => {
    const target = document.getElementById(focus) || document.getElementById('incident-evidence')
    if (target) {
      event.preventDefault()
      target.scrollIntoView({ block: 'center' })
    }
  }}>核查原始记录</a>
}

export function DiagnosisReferences({ references, index }) {
  if (!references?.length) return <Typography.Text type="secondary">这条假设没有有效的证据引用。</Typography.Text>
  return <ul className="operations-items">
    {[...new Set(references)].map(ref => {
      const entry = index?.find(item => item.id === ref)
      return <li key={ref}>
        <Tag>{ref}</Tag>
        {entry ? <Space wrap>
          <strong>{entry.label}</strong>
          <Typography.Text type="secondary">{entry.observed_at ? operationTime(entry.observed_at) : '样本时间未记录'}</Typography.Text>
          <EvidenceAnchor entry={entry} />
          {Object.entries(entry.preview || {}).map(([key, value]) => <Tag key={key}>{key} {String(value)}</Tag>)}
        </Space> : <Typography.Text type="warning">历史索引缺失或该引用未包含在证据包里</Typography.Text>}
      </li>
    })}
  </ul>
}

export function DiagnosisUncertainty({ rca }) {
  const items = rca.llm?.need_more_evidence || []
  const alternatives = (rca.root_causes || []).slice(1)
  return <>
    {rca.llm?.summary && <Alert type="info" showIcon message="AI 综合研判" description={rca.llm.summary} />}
    {items.length > 0 && <div><strong>尚需补充的证据</strong><ul>{items.map((item, i) => <li key={i}>{item}</li>)}</ul></div>}
    {alternatives.length > 0 && <Typography.Paragraph type="secondary">
      备选解释 {alternatives.map(root => root.name || root.rule_id).join('、')}
    </Typography.Paragraph>}
  </>
}

export function OamEvidenceLinks({ references, index, trailHref = '' }) {
  if (!Array.isArray(references) || !references.length) return <Typography.Text type="secondary">未记录引用。</Typography.Text>
  return <ul className="operations-items">{[...new Set(references)].map(ref => {
    const item = index?.find(entry => entry.id === ref)
    const link = Number.isInteger(item?.turn) ? `${trailHref}#oam-turn-${item.turn}` : null
    return <li key={ref}>
      <Tag>{ref}</Tag>
      {item ? <Space wrap>
        {link && <Link to={link}>查看第 {item.turn} 轮观测</Link>}
        <Typography.Text type="secondary">{item.tool || '工具未记录'} · {operationTime(item.observed_at)}</Typography.Text>
      </Space> : <Typography.Text type="warning">此轨迹没有对应观测</Typography.Text>}
    </li>
  })}</ul>
}
