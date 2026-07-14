import React from 'react';
import { Tag } from 'antd';
import { waitClassColor, waitClassLabel } from './waitClassMeta';

/** 等待类色块标签 (契约组件, 禁止各页自配色) */
export default function WaitClassTag({ value }) {
  if (!value) return <Tag>-</Tag>;
  return <Tag color={waitClassColor(value)} style={{ marginInlineEnd: 0 }}>{waitClassLabel(value)}</Tag>;
}
