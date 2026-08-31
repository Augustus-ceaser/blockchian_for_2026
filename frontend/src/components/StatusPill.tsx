import { Tag } from 'antd'

const colors: Record<string, string> = {
  在线: 'success',
  维护中: 'warning',
  成功: 'success',
  待处理: 'warning',
  受控: 'processing',
  已生效: 'success',
  待签署: 'warning',
  已批准: 'success',
  审核中: 'processing',
  已提交: 'processing',
  运行中: 'processing',
  待结果审查: 'warning',
  已发布: 'success',
}

export function StatusPill({ value }: { value: string }) {
  return (
    <Tag variant="filled" color={colors[value] ?? 'default'}>
      {value}
    </Tag>
  )
}
