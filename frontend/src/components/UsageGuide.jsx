import { Alert, Button, Drawer, Space, Steps, Typography } from 'antd';
import { useNavigate } from 'react-router-dom';
import { canAccessRoute } from '../utils/permission';

const steps = [
  ['纳管数据库', '填写连接信息，测试当次连通性，再保存配置。保存不代表已采集成功。', '/databases'],
  ['确认监控数据', '检查数据来源、更新时间和实例最后采集时间。未知和未评分不能当作正常。', '/'],
  ['跟进告警与事故', '从值班队列查看待确认事项，核对证据后交接或跟进。', '/work-queue'],
  ['审批与执行', '核对目标、影响、风险和回滚方式。AI 建议不会自动替代授权与审批。', '/tickets'],
  ['复盘与报告', '检查恢复后的监控结果，再生成报告；不要仅凭执行成功判断故障已恢复。', '/reports'],
];
export default function UsageGuide({ open, onClose }) {
  const navigate = useNavigate();
  return <Drawer title="使用指南 · 从纳管到闭环" open={open} onClose={onClose} width={480}>
    <Alert type="info" showIcon message="先看证据，再做变更" description="入口按当前账号权限显示；缺少权限请联系管理员。" style={{ marginBottom: 24 }} />
    <Steps direction="vertical" current={-1} items={steps.map(([title, text, path]) => ({ title, description: <Space direction="vertical" style={{ marginBottom: 20 }}>
      <Typography.Paragraph>{text}</Typography.Paragraph>
      {canAccessRoute(path) && <Button onClick={() => { onClose(); navigate(path); }}>前往{title}</Button>}
    </Space> }))} />
    <Typography.Text type="secondary">请求失败时先重试；若仍失败，向管理员提供页面、发生时间和请求编号。请勿分享数据库密码或访问令牌。</Typography.Text>
  </Drawer>;
}
