import React, { useMemo, useCallback } from 'react';
import * as echarts from 'echarts/core';
import { LineChart } from 'echarts/charts';
import {
  GridComponent, TooltipComponent, LegendComponent, DataZoomComponent,
  MarkLineComponent, BrushComponent, ToolboxComponent,
} from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import ReactEChartsCore from 'echarts-for-react/lib/core';
import { WAIT_CLASS_META, WAIT_CLASS_ORDER } from './waitClassMeta';

echarts.use([LineChart, GridComponent, TooltipComponent, LegendComponent,
  DataZoomComponent, MarkLineComponent, BrushComponent, ToolboxComponent,
  CanvasRenderer]);

/**
 * AAS 按等待类堆叠面积图 (phase7/30 §2.2 契约)。
 * props:
 *  - series: [{key, points:[[iso, aas],...]}]  (perf/aas 响应)
 *  - cpuCores: number|null → Max CPU 红虚线
 *  - height: px (默认 320)
 *  - onRangeChange(fromISO, toISO): dataZoom/brush 选窗回调 (联动 Top 表)
 *  - brush: true 启用框选 (顶级活动页)
 */
export default function AasChart({ series = [], cpuCores = null, height = 320,
                                   onRangeChange, brush = false, extraKeys = null }) {
  const keys = useMemo(() => {
    const present = new Set(series.map((s) => s.key));
    const ordered = (extraKeys || WAIT_CLASS_ORDER).filter((k) => present.has(k));
    // 非等待类维度 (user/sql) 的 key 不在九类中 → 按原序全画
    const others = series.map((s) => s.key).filter((k) => !ordered.includes(k));
    return [...ordered, ...others];
  }, [series, extraKeys]);

  const option = useMemo(() => {
    const byKey = Object.fromEntries(series.map((s) => [s.key, s.points]));
    const opt = {
      color: keys.map((k) => WAIT_CLASS_META[k]?.color || undefined),
      tooltip: {
        trigger: 'axis',
        valueFormatter: (v) => (v == null ? '-' : Number(v).toFixed(2)),
      },
      legend: { data: keys.map((k) => WAIT_CLASS_META[k]?.label || k), bottom: 0, type: 'scroll' },
      grid: { left: 56, right: 24, top: 28, bottom: 52 },
      xAxis: { type: 'time' },
      yAxis: { type: 'value', name: 'AAS', minInterval: 1 },
      dataZoom: [{ type: 'inside' }, { type: 'slider', height: 16, bottom: 24 }],
      series: keys.map((k) => ({
        name: WAIT_CLASS_META[k]?.label || k,
        type: 'line', stack: 'aas', symbol: 'none',
        areaStyle: { opacity: 0.85 }, lineStyle: { width: 0 }, emphasis: { focus: 'series' },
        data: (byKey[k] || []).map(([t, v]) => [t, v]),
      })),
    };
    if (cpuCores != null && opt.series.length) {
      opt.series.push({
        name: 'Max CPU', type: 'line', symbol: 'none', silent: true, data: [],
        markLine: {
          symbol: 'none', lineStyle: { color: '#d40000', type: 'dashed', width: 1.5 },
          label: { formatter: `CPU 核数 ${cpuCores}`, position: 'insideEndTop' },
          data: [{ yAxis: cpuCores }],
        },
      });
    }
    if (brush) {
      opt.toolbox = { feature: { brush: { type: ['lineX', 'clear'] } }, right: 8 };
      opt.brush = { xAxisIndex: 0, brushLink: 'all', outOfBrush: { colorAlpha: 0.25 } };
    }
    return opt;
  }, [series, keys, cpuCores, brush]);

  const debounced = useMemo(() => {
    let timer = null;
    return (fn) => { clearTimeout(timer); timer = setTimeout(fn, 400); };
  }, []);

  const onEvents = useMemo(() => ({
    datazoom: (params, chart) => {
      if (!onRangeChange) return;
      debounced(() => {
        try {
          const opt = chart.getOption();
          const axis = opt.dataZoom?.[0];
          if (!axis) return;
          const data = opt.series?.[0]?.data || [];
          if (!data.length) return;
          const t0 = new Date(data[0][0]).getTime();
          const t1 = new Date(data[data.length - 1][0]).getTime();
          const f = t0 + ((axis.start ?? 0) / 100) * (t1 - t0);
          const t = t0 + ((axis.end ?? 100) / 100) * (t1 - t0);
          onRangeChange(new Date(f).toISOString(), new Date(t).toISOString());
        } catch { /* ignore */ }
      });
    },
    brushend: (params) => {
      if (!onRangeChange) return;
      const range = params?.areas?.[0]?.coordRange;
      if (range && range.length === 2) {
        onRangeChange(new Date(range[0]).toISOString(), new Date(range[1]).toISOString());
      }
    },
  }), [onRangeChange, debounced]);

  return (
    <ReactEChartsCore echarts={echarts} option={option} notMerge
      style={{ height }} onEvents={onEvents} />
  );
}
