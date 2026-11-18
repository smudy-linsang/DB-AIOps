import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { safeReturnTo, readStoredUser } from '../utils/experience';
vi.mock('echarts-for-react/esm/core', () => ({ default: () => <div>趋势图</div> }));
vi.mock('../services/api', () => ({
  healthAPI: { check: vi.fn() }, databaseAPI: { list: vi.fn() }, alertAPI: { list: vi.fn() },
  dashboardAPI: { getCharts: vi.fn(), getStats: vi.fn() },
}));
import { healthAPI, databaseAPI, alertAPI, dashboardAPI } from '../services/api';
import Dashboard from '../pages/Dashboard';

beforeEach(() => {
  vi.clearAllMocks();
  healthAPI.check.mockResolvedValue({ status: 'healthy' });
  databaseAPI.list.mockResolvedValue({ databases: [] });
  alertAPI.list.mockResolvedValue({ alerts: [] });
  dashboardAPI.getCharts.mockResolvedValue({ trend: [], source: 'orm' });
  dashboardAPI.getStats.mockResolvedValue({ total: 2, online: 1, offline: 1, degraded: 0, unknown: 0, avg_health: null, scored_count: 0, active_alerts: 0 });
});
describe('可恢复与可信体验', () => {
  it.each(['https://evil.example', '//evil.example', '/\\evil', '/login', '/login?returnTo=/x', '/x\ny'])('拒绝不安全跳转 %s', value => expect(safeReturnTo(value)).toBe('/'));
  it('保留站内路径、查询和锚点', () => expect(safeReturnTo('/databases?type=mysql#list')).toBe('/databases?type=mysql#list'));
  it('损坏的用户缓存不导致白屏', () => { localStorage.setItem('user', '{'); expect(readStoredUser()).toBeNull(); });
  it('窗口选择传入后端', async () => {
    render(<MemoryRouter><Dashboard /></MemoryRouter>);
    await waitFor(() => expect(dashboardAPI.getCharts).toHaveBeenCalledWith(24));
    fireEvent.click(screen.getByText('6小时'));
    await waitFor(() => expect(dashboardAPI.getCharts).toHaveBeenCalledWith(6));
  });
  it('失败显示警告并保留成功的旧数据，重试后恢复', async () => {
    render(<MemoryRouter><Dashboard /></MemoryRouter>);
    await screen.findByText('2');
    dashboardAPI.getStats.mockRejectedValueOnce(new Error('统计不可用'));
    fireEvent.click(screen.getByText('刷新数据'));
    await screen.findByText('部分数据未更新，请勿据此判断系统正常');
    expect(screen.getByText('失败 · 保留旧数据')).toBeInTheDocument();
    expect(screen.getByText('2')).toBeInTheDocument();
    fireEvent.click(screen.getByText('刷新数据'));
    await waitFor(() => expect(screen.queryByText('部分数据未更新，请勿据此判断系统正常')).not.toBeInTheDocument());
  });
  it('初次统计失败不得显示虚假的零告警', async () => {
    dashboardAPI.getStats.mockRejectedValue(new Error('统计不可用'));
    render(<MemoryRouter><Dashboard /></MemoryRouter>);
    await screen.findByText('统计不可用');
    expect(screen.getAllByText('—').length).toBeGreaterThan(0);
    expect(screen.queryByText('0 个实例已有今日评分')).not.toBeInTheDocument();
  });
});
