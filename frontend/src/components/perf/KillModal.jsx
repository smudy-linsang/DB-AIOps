import React, { useState } from 'react';
import { Alert, Button, Input, Modal, Space, Steps, Typography, message } from 'antd';
import { auditLogAPI, perfAPI } from '../../services/api';

const { Text } = Typography;

/**
 * 申请终止会话弹窗 (phase7/30 §6): 走 AuditLog 审批执行链, 两步留痕。
 * 有审批权限的用户可同屏"审批并执行"。
 */
export default function KillModal({ configId, target, onClose, onDone }) {
  const [reason, setReason] = useState('');
  const [audit, setAudit] = useState(null);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setBusy(true);
    try {
      const r = await perfAPI.killSession(configId, target.session_id, reason);
      setAudit(r.data);
      message.success(`审批工单已创建 #${r.data.audit_id}`);
    } catch (e) {
      message.error(`提交失败: ${e.message}`);
    } finally {
      setBusy(false);
    }
  };

  const approveAndRun = async () => {
    setBusy(true);
    try {
      await auditLogAPI.approve(audit.audit_id);
      await auditLogAPI.execute(audit.audit_id);
      message.success('已审批并执行');
      close(true);
    } catch (e) {
      message.error(`审批/执行失败: ${e.message}`);
      setBusy(false);
    }
  };

  const close = (done) => {
    setReason(''); setAudit(null); setBusy(false);
    onClose?.();
    if (done) setTimeout(() => onDone?.(), 3000);
  };

  return (
    <Modal open={!!target} onCancel={() => close(false)} footer={null}
           title={`申请终止会话 ${target?.session_id || ''}`} destroyOnClose>
      <Steps size="small" current={audit ? 1 : 0} items={[
        { title: '提交申请' }, { title: '审批并执行' },
      ]} style={{ marginBottom: 16 }} />
      {!audit ? (
        <Space direction="vertical" style={{ width: '100%' }}>
          <Alert type="warning" showIcon
                 message="终止会话是写操作, 需走审批链留痕 (安全铁律)" />
          <Input.TextArea rows={2} placeholder="终止理由 (必填), 如: 阻塞源, 等待 120s"
                          value={reason} onChange={(e) => setReason(e.target.value)} />
          <Button type="primary" danger block loading={busy}
                  disabled={!reason.trim()} onClick={submit}>提交审批工单</Button>
        </Space>
      ) : (
        <Space direction="vertical" style={{ width: '100%' }}>
          <Alert type="info" showIcon message={`工单 #${audit.audit_id} 待审批`}
                 description={<Text code>{audit.sql_command}</Text>} />
          <Button type="primary" danger block loading={busy} onClick={approveAndRun}>
            我有审批权限: 审批并执行
          </Button>
          <Button block onClick={() => close(false)}>留待他人审批</Button>
        </Space>
      )}
    </Modal>
  );
}
