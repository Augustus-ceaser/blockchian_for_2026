import { CheckOutlined } from '@ant-design/icons'
import { stageLabels, stageOrder } from '../mock/data'
import { useDemo } from '../mock/DemoContext'

export function FlowProgress() {
  const { stage } = useDemo()
  const currentIndex = stageOrder.indexOf(stage)

  return (
    <div className="flow-progress">
      {stageOrder.map((item, index) => (
        <div
          className={`flow-progress__item ${index <= currentIndex ? 'is-active' : ''} ${index === currentIndex ? 'is-current' : ''}`}
          key={item}
        >
          <div className="flow-progress__dot">{index < currentIndex ? <CheckOutlined /> : index + 1}</div>
          <div className="flow-progress__label">{stageLabels[item]}</div>
          {index < stageOrder.length - 1 && <div className="flow-progress__line" />}
        </div>
      ))}
    </div>
  )
}
