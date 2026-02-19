import './index.css';
import { Popover } from 'antd';
import Info from '../components/info';
import ChatBot from '../components/chatBot';

import FabGPT from '../components/fabGPT';
import { menuConfig } from '../../config';
import classNames from 'classnames';

// 添加知识库
import { DatabaseOutlined } from '@ant-design/icons';

const MenuItem = (props) => {
  const { isSelected, name, onClick, subSelectKey } = props;

  const menuItem = menuConfig.find(menu => menu.key === name)

  const handleClick = (name, subName) => {
    onClick && onClick(name, subName)
  }

  const renderPopoverContent = () => {
    return (
      <div className='popover-content'>
        {menuItem.children.length ? (
          menuItem.children.map((item) => (
            <div onClick={() => handleClick(name, item.key)} className={classNames('popover-item', isSelected && subSelectKey === item.key && 'popover-item-select')}>{item.name}</div>
          ))
        ) : (
          <div onClick={() => handleClick(name, name)} className={classNames('popover-item', isSelected && 'popover-item-select')}>{menuItem.name}</div>
        )}
      </div>
    );
  };

  return (
    <Popover placement='right' title='' content={renderPopoverContent()} trigger={'hover'}>
      <div className='menu-item'>
        {name === 'ClassIntroduce' && (
          <Info size={18} isSelected={isSelected}></Info>
        )}
        {name === 'ChatBot' && (
          <ChatBot size={18} isSelected={isSelected}></ChatBot>
        )}
        {name === 'FabGPT' && (
          <FabGPT size={18} isSelected={isSelected}></FabGPT>
        )}
        {/* 额外添加RagManager */}
        {name === 'RagManager' && (
          <DatabaseOutlined 
            style={{ 
              fontSize: 18, 
              color: isSelected ? '#1890ff' : 'rgba(0, 0, 0, 0.65)' 
            }} 
          />
        )}
      </div>
    </Popover>
  );
}

export default MenuItem;