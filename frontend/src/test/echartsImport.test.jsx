import React from 'react'
import { describe, it, expect, vi } from 'vitest'
import { render } from '@testing-library/react'
import ReactEChartsCore from 'echarts-for-react/esm/core'
import ReactEChartsCoreViaAlias from 'echarts-for-react/lib/core'
import zhCN from 'antd/es/locale/zh_CN'
import zhCNViaAlias from 'antd/locale/zh_CN'

describe('Vite 8 ESM interop regression', () => {
  it('ReactEChartsCore from esm/core and aliased lib/core are valid React components', () => {
    expect(typeof ReactEChartsCore).toBe('function')
    expect(typeof ReactEChartsCoreViaAlias).toBe('function')
    expect(ReactEChartsCore).toBe(ReactEChartsCoreViaAlias)
  })

  it('antd zh_CN locale exposes locale values directly rather than a wrapper object', () => {
    expect(zhCN?.locale).toBe('zh-cn')
    expect(zhCNViaAlias?.locale).toBe('zh-cn')
  })

  it('ReactEChartsCore mounts with echarts instance without invalid element type errors', () => {
    const mockInstance = {
      setOption: vi.fn(),
      resize: vi.fn(),
      dispose: vi.fn(),
      on: vi.fn(),
      off: vi.fn(),
      isDisposed: () => false,
    }
    const mockEcharts = {
      init: vi.fn(() => mockInstance),
      getInstanceByDom: vi.fn(() => mockInstance),
      dispose: vi.fn(),
    }

    const { container } = render(
      <ReactEChartsCore
        echarts={mockEcharts}
        option={{ series: [{ type: 'line', data: [1, 2, 3] }] }}
        style={{ height: 240 }}
      />
    )
    expect(container.querySelector('.echarts-for-react')).toBeTruthy()
  })
})
