import './index.css';

const Card = (props) => {
  const { title, content, style } = props

  return (
    <div className='card' style={style}>
      <div className='title'>{title}</div>
      <div className='content'>
        {content}
      </div>
    </div>
  )
}

export default Card;