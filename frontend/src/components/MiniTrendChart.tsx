import { useEffect, useRef } from 'react'
import { LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import { init, use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'

use([LineChart, GridComponent, TooltipComponent, CanvasRenderer])

export function MiniTrendChart() {
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!ref.current) return
    const chart = init(ref.current)
    chart.setOption({
      animationDuration: 500,
      grid: { top: 18, left: 30, right: 12, bottom: 24 },
      tooltip: { trigger: 'axis' },
      xAxis: {
        type: 'category',
        boundaryGap: false,
        data: ['09:00', '09:20', '09:40', '10:00', '10:20', '10:40'],
        axisLine: { lineStyle: { color: '#d9e2ec' } },
        axisLabel: { color: '#7b8794', fontSize: 11 },
        axisTick: { show: false },
      },
      yAxis: {
        type: 'value',
        minInterval: 1,
        axisLabel: { color: '#7b8794', fontSize: 11 },
        splitLine: { lineStyle: { color: '#eef2f6' } },
      },
      series: [
        {
          type: 'line',
          smooth: true,
          showSymbol: false,
          data: [1, 3, 4, 7, 9, 12],
          lineStyle: { color: '#1769aa', width: 2 },
          areaStyle: { color: 'rgba(23, 105, 170, 0.09)' },
        },
      ],
    })
    const resize = () => chart.resize()
    window.addEventListener('resize', resize)
    return () => {
      window.removeEventListener('resize', resize)
      chart.dispose()
    }
  }, [])

  return <div className="mini-chart" ref={ref} aria-label="近两小时可信流通活动趋势图" />
}
