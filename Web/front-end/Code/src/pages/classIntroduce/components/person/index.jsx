import { Avatar } from 'antd';
import './index.css';

const Person = (props) => {
  const { data } = props
  const { title, email, research, avatar, link } = data || {};

  return (
    <div className='person'>
      <Avatar src={avatar} size={88}></Avatar>
      <div className='detail'>
        <div className='header' onClick={() => window.open(link)}>{title}</div>
        <div className='email'>{email}</div>
        <div className='research'>研究领域：</div>
        <div className='research-list'>
          {research?.map((item) => {
            return (
              <div className='research-item'>
                <div className='research-dot'></div>
                <div className='research-info'>{item}</div>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}

export default Person;