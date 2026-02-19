import { classLink } from '../../config';
import './index.css';
import { Tooltip } from 'antd';

const ClassItem = (props) => {
  const { index, content } = props;

  return (
    <div className='class-item'>
      <div className='class-index'>{index}</div>
      <div className='class-right' onClick={() => window.open(classLink, '_blank', 'noopener,noreferrer')}>
        <Tooltip title={content}>
          <div className='class-content'>{content}</div>
        </Tooltip>
        <div className='class-arrow'></div>
      </div>
    </div>
  )
}

export default ClassItem;
