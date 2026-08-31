import { ArrowRightOutlined, DatabaseOutlined, SafetyCertificateOutlined, SearchOutlined } from '@ant-design/icons'
import { Button, Input, Select, Space, Tag } from 'antd'
import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { PageHeading } from '../components/PageHeading'
import { products } from '../mock/data'

export function ProductsPage() {
  const navigate = useNavigate()
  const [query, setQuery] = useState('')
  const [disease, setDisease] = useState('全部癌种')
  const filtered = useMemo(
    () => products.filter((product) =>
      (disease === '全部癌种' || product.disease === disease)
      && `${product.name}${product.summary}${product.modalities.join('')}`.toLowerCase().includes(query.toLowerCase()),
    ),
    [query, disease],
  )

  return (
    <div className="page-stack">
      <PageHeading
        eyebrow="可信数据产品目录"
        title="发现可按约使用的数据产品"
        description="目录仅展示产品级元数据和使用规则，不展示患者级数据或原始切片。"
      />
      <div className="catalog-toolbar">
        <Input
          prefix={<SearchOutlined />}
          placeholder="搜索癌种、数据模态或研究用途"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          allowClear
        />
        <Select
          value={disease}
          onChange={setDisease}
          options={['全部癌种', ...new Set(products.map((item) => item.disease))].map((item) => ({ label: item, value: item }))}
        />
        <Select value="受控计算" options={[{ label: '受控计算', value: '受控计算' }]} />
      </div>
      <div className="catalog-summary">
        <span>共找到 <strong>{filtered.length}</strong> 个数据产品</span>
        <span><SafetyCertificateOutlined /> 目录已隐藏个体级敏感信息</span>
      </div>
      <div className="product-grid">
        {filtered.map((product, index) => (
          <article className={`product-card ${index === 0 ? 'product-card--featured' : ''}`} key={product.id}>
            <div className="product-card__topline">
              <span className="product-card__icon"><DatabaseOutlined /></span>
              <Space size={6} wrap>
                <Tag color="blue" bordered={false}>{product.useMode}</Tag>
                {index === 0 && <Tag color="cyan" bordered={false}>主演示产品</Tag>}
              </Space>
            </div>
            <h3>{product.name}</h3>
            <p>{product.summary}</p>
            <div className="product-card__provider">
              <span>数据提供方</span>
              <strong>{product.provider}</strong>
            </div>
            <div className="product-card__stats">
              <div><strong>{product.caseCount.toLocaleString()}</strong><span>病例索引</span></div>
              <div><strong>{product.slideCount.toLocaleString()}</strong><span>切片索引</span></div>
              <div><strong>{product.version}</strong><span>产品版本</span></div>
            </div>
            <div className="tag-list">
              {product.modalities.map((item) => <Tag key={item}>{item}</Tag>)}
            </div>
            <div className="product-card__policy"><SafetyCertificateOutlined /> {product.classification}</div>
            <Button type={index === 0 ? 'primary' : 'default'} block onClick={() => navigate(`/products/${product.id}`)}>
              查看产品与使用规则 <ArrowRightOutlined />
            </Button>
          </article>
        ))}
      </div>
    </div>
  )
}
