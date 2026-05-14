import React, { useState, useCallback } from 'react';
import { Radio, DatePicker, Button, Space, Typography } from 'antd';
import {
  ClockCircleOutlined,
  ReloadOutlined,
} from '@ant-design/icons';
import dayjs from 'dayjs';

const { RangePicker } = DatePicker;
const { Text } = Typography;

/**
 * 预设时间范围定义
 * key: 用于标识的值
 * label: 显示文本
 * getRange: 返回 [start, end] dayjs 对象
 */
export const TIME_RANGE_PRESETS = {
  '15m': { label: '15分钟', getRange: () => [dayjs().subtract(15, 'minute'), dayjs()] },
  '30m': { label: '30分钟', getRange: () => [dayjs().subtract(30, 'minute'), dayjs()] },
  '1h': { label: '1小时', getRange: () => [dayjs().subtract(1, 'hour'), dayjs()] },
  '4h': { label: '4小时', getRange: () => [dayjs().subtract(4, 'hour'), dayjs()] },
  '12h': { label: '12小时', getRange: () => [dayjs().subtract(12, 'hour'), dayjs()] },
  '24h': { label: '24小时', getRange: () => [dayjs().subtract(24, 'hour'), dayjs()] },
  '3d': { label: '3天', getRange: () => [dayjs().subtract(3, 'day'), dayjs()] },
  '7d': { label: '7天', getRange: () => [dayjs().subtract(7, 'day'), dayjs()] },
  '30d': { label: '30天', getRange: () => [dayjs().subtract(30, 'day'), dayjs()] },
};

/**
 * 自动刷新间隔选项
 */
export const AUTO_REFRESH_OPTIONS = [
  { value: 0, label: '关闭' },
  { value: 15, label: '15秒' },
  { value: 30, label: '30秒' },
  { value: 60, label: '1分钟' },
  { value: 300, label: '5分钟' },
];

/**
 * 时间范围选择器组件
 * 
 * 提供预设时间范围快速选择 + 自定义日期范围选择 + 自动刷新功能
 * Oracle EMCC 13c 风格：时间选择器在页面顶部操作栏中
 * 
 * @param {Object} props
 * @param {string} props.value - 当前选中的时间范围 key（如 '1h', '24h', '7d'）
 * @param {Array} props.customRange - 自定义范围 [dayjs, dayjs]
 * @param {Function} props.onChange - 时间范围变更回调 (presetKey, [start, end], isAutoRefresh?)
 * @param {number} props.autoRefresh - 自动刷新间隔（秒），0 表示关闭
 * @param {Function} props.onAutoRefreshChange - 自动刷新变更回调
 * @param {boolean} props.loading - 是否加载中
 * @param {Function} props.onRefresh - 手动刷新回调
 * @param {Array} props.customPresets - 自定义预设时间范围 keys（默认全部显示）
 * @param {boolean} props.showAutoRefresh - 是否显示自动刷新选项，默认 true
 * @param {string} props.size - 组件尺寸 'small' | 'middle' | 'large'
 */
function TimeRangeSelector({
  value = '1h',
  customRange = null,
  onChange,
  autoRefresh = 0,
  onAutoRefreshChange,
  loading = false,
  onRefresh,
  customPresets = null,
  showAutoRefresh = true,
  size = 'small',
}) {
  const [isCustomMode, setIsCustomMode] = useState(false);
  const [localCustomRange, setLocalCustomRange] = useState(null);

  // 显示哪些预设按钮
  const presetsToShow = customPresets || Object.keys(TIME_RANGE_PRESETS);

  const handlePresetClick = useCallback(
    (key) => {
      setIsCustomMode(false);
      const { getRange } = TIME_RANGE_PRESETS[key];
      if (getRange) {
        const [start, end] = getRange();
        onChange?.(key, [start, end]);
      }
    },
    [onChange]
  );

  const handleCustomRangeOk = useCallback(
    (dates) => {
      if (dates && dates[0] && dates[1]) {
        onChange?.('custom', [dates[0], dates[1]]);
      }
    },
    [onChange]
  );

  const handleRangePickerOpen = useCallback(() => {
    setIsCustomMode(true);
    setLocalCustomRange(customRange);
  }, [customRange]);

  return (
    <Space size={size === 'small' ? 4 : 8} wrap>
      {/* 预设时间范围按钮 */}
      <Radio.Group
        value={isCustomMode ? 'custom' : value}
        onChange={(e) => {
          if (e.target.value === 'custom') {
            setIsCustomMode(true);
          } else {
            handlePresetClick(e.target.value);
          }
        }}
        size={size}
        buttonStyle="solid"
      >
        {presetsToShow.map((key) => (
          <Radio.Button key={key} value={key}>
            {TIME_RANGE_PRESETS[key]?.label || key}
          </Radio.Button>
        ))}
        <Radio.Button value="custom">
          <ClockCircleOutlined style={{ marginRight: 4 }} />
          自定义
        </Radio.Button>
      </Radio.Group>

      {/* 自定义日期范围选择器 */}
      {isCustomMode && (
        <RangePicker
          showTime={{ format: 'HH:mm' }}
          format="YYYY-MM-DD HH:mm"
          value={customRange}
          onChange={(dates) => setLocalCustomRange(dates)}
          onOk={handleCustomRangeOk}
          size={size}
          presets={[
            { label: '今天', value: [dayjs().startOf('day'), dayjs()] },
            { label: '昨天', value: [dayjs().subtract(1, 'day').startOf('day'), dayjs().subtract(1, 'day').endOf('day')] },
            { label: '本周', value: [dayjs().startOf('week'), dayjs()] },
            { label: '上周', value: [dayjs().subtract(1, 'week').startOf('week'), dayjs().subtract(1, 'week').endOf('week')] },
            { label: '本月', value: [dayjs().startOf('month'), dayjs()] },
            { label: '上月', value: [dayjs().subtract(1, 'month').startOf('month'), dayjs().subtract(1, 'month').endOf('month')] },
          ]}
        />
      )}

      {/* 自动刷新 */}
      {showAutoRefresh && (
        <Radio.Group
          value={autoRefresh}
          onChange={(e) => onAutoRefreshChange?.(e.target.value)}
          size={size}
          optionType="default"
        >
          {AUTO_REFRESH_OPTIONS.map((opt) => (
            <Radio.Button key={opt.value} value={opt.value}>
              {opt.label === '关闭' ? '🔄关' : opt.label}
            </Radio.Button>
          ))}
        </Radio.Group>
      )}

      {/* 手动刷新按钮 */}
      {onRefresh && (
        <Button
          icon={<ReloadOutlined spin={loading} />}
          size={size}
          onClick={onRefresh}
          loading={loading}
        >
          刷新
        </Button>
      )}

      {/* 当前时间范围提示 */}
      {customRange && customRange[0] && customRange[1] && (
        <Text type="secondary" style={{ fontSize: 12 }}>
          {customRange[0].format('YYYY-MM-DD HH:mm')} ~ {customRange[1].format('YYYY-MM-DD HH:mm')}
        </Text>
      )}
    </Space>
  );
}

export default TimeRangeSelector;
