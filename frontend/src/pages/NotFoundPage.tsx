import { Button, Result } from 'antd'
import { useNavigate } from 'react-router-dom'

export function NotFoundPage() {
  const navigate = useNavigate()
  return <Result status="404" title="页面不存在" subTitle="该原型页面尚未创建或链接已失效。" extra={<Button type="primary" onClick={() => navigate('/overview')}>返回工作台</Button>} />
}
