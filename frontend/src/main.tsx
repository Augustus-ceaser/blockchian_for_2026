import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import { RouterProvider } from 'react-router-dom'
import { DemoProvider } from './mock/DemoContext'
import { DataModeProvider } from './api/DataModeContext'
import { router } from './router'
import './styles.css'
import { RoadshowProvider } from './roadshow/RoadshowContext'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ConfigProvider
      locale={zhCN}
      theme={{
        token: {
          colorPrimary: '#087ea4',
          colorInfo: '#087ea4',
          colorSuccess: '#0f8a72',
          colorWarning: '#b76a0b',
          colorError: '#c34444',
          colorText: '#102a3a',
          colorTextSecondary: '#647887',
          colorBorder: '#dce6ec',
          colorBgLayout: '#f5f7fa',
          borderRadius: 12,
          borderRadiusSM: 8,
          borderRadiusLG: 16,
          fontFamily: "'Segoe UI Variable', Inter, 'PingFang SC', 'Microsoft YaHei', system-ui, sans-serif",
          boxShadowSecondary: '0 16px 44px rgba(13, 43, 61, 0.08)',
        },
        components: {
          Button: { controlHeightLG: 44, fontWeight: 600 },
          Card: { headerFontSize: 16, headerBg: '#ffffff' },
          Drawer: { colorBgElevated: '#ffffff' },
          Menu: { itemBorderRadius: 8, itemHeight: 46 },
          Table: { headerBg: '#f6f9fb', headerColor: '#425d6d' },
        },
      }}
    >
      <DataModeProvider>
        <RoadshowProvider>
          <DemoProvider>
            <RouterProvider router={router} />
          </DemoProvider>
        </RoadshowProvider>
      </DataModeProvider>
    </ConfigProvider>
  </StrictMode>,
)
