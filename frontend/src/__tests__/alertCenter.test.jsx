import { App } from 'antd'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import AlertList from '../pages/AlertList'
import { alertAPI } from '../services/api'

vi.mock('../services/api', () => ({ alertAPI: { list: vi.fn() } }))

it('opens the selected alert and shows the measured zero in its recovery details', async () => {
  alertAPI.list.mockResolvedValue({ alerts: [{
    id: 7, title: '连接用量告警', status: 'resolved', severity: 'warning', db_name: '测试库',
    metric_key: 'conn_usage_pct', recovery_evidence: { source: 'collector', value: 0, threshold: 80 },
  }] })

  render(<App><AlertList /></App>)
  fireEvent.click(screen.getByRole('tab', { name: '已恢复' }))
  fireEvent.click(await screen.findByRole('button', { name: /详\s*情/ }))

  const detail = await screen.findByRole('dialog', { name: '告警详情' })
  await waitFor(() => expect(within(detail).getByText('连接用量告警')).toBeVisible())
  expect(within(detail).getByText('恢复依据')).toBeVisible()
  expect(within(detail).getByText('采集器实测')).toBeVisible()
  expect(within(detail).getByText('0', { exact: true })).toBeVisible()
})
