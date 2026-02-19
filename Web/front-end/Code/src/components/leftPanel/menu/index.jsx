import './index.css';
import { menuConfig } from '../config';
import MenuItem from './menuItem';
import { Avatar, Popover } from 'antd';
import logo from '../../../assets/logo.png'
import Cookies from 'js-cookie';
import classnames from 'classnames'
import { useState } from 'react';
import { useDispatch } from "react-redux";
import { updateMainPage } from '../../../store/pageStore';
import { useNavigate } from 'react-router-dom';

const Menu = () => {
  const avatarName = (Cookies.get('user') || '')?.split('')?.pop(); // 用户名的最后一个字符
  // const avatarName = (Cookies.get('user') || '')?.[0] //用户名第一个字符
  const [selectKey, setSelectkeys] = useState('ClassIntroduce')
  const [subSelectKey, setSelectSubKey] = useState('')

  const dispatch = useDispatch();
  const navigate = useNavigate();

  const handleMainPageChange = (pageName, subPageName) => {
    setSelectkeys(pageName)
    setSelectSubKey(subPageName)
    dispatch(updateMainPage({ Main_Page: pageName, Sub_Page: subPageName || "" }));
  };

  const handleLoginOut = () => {
    localStorage.clear();
    Cookies.remove('user');
    for (let index = 1; index <= 5; index++) {
      Cookies.remove(index);
      
    }
    dispatch(updateMainPage({ Main_Page: 'ClassIntroduce' }));

    navigate('/login');
  }

  // menuConfig:

  return (
    <div className='left-menu'>
      <Avatar src={logo} size={34}></Avatar>
      <div className={classnames('line', 'first')}></div>
      <div className='menu-list'>
        {
          menuConfig.map((item) => <MenuItem key={item.key} subSelectKey={subSelectKey} onClick={(name, subName) => handleMainPageChange(name, subName)} name={item.key} isSelected={selectKey === item.key || item.children.includes(item => item.key === selectKey)}></MenuItem>)
        }
      </div>
      <div className={classnames('line', 'last')}></div>
      <Popover placement='right' title='' content={<div onClick={handleLoginOut} style={{ cursor: 'pointer' }}>退出登录</div>} trigger={'hover'}>
        <div className='left-panel-user'>
          <Avatar size={40} style={{ backgroundColor: '#355998' }}>{avatarName}</Avatar>
        </div>
      </Popover>
    </div>
  )
}

export default Menu;