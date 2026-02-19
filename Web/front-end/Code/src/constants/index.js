export const MESSAGE_TYPE = {
  BOT: 'bot',
  USER: 'user'
}

export const MESSAGE_AVATAR = {
  bot: require('../assets/bot.png'),
  user: require('../assets/logo.png')
}

// 动态思考文本数组
export const THINKING_TEXTS = [
  '思考中.',
  '思考中..',
  '思考中...',
  '正在思考.',
  '正在思考..',
  '正在思考...',
  '正在分析.',
  '正在分析..',
  '正在分析...'
];

// 临时机器人信息
export const botInfo = {
  content: '思考中...',
  sender: MESSAGE_TYPE.BOT,
  loading: true,
};

// 动态思考机器人信息生成函数
export const createDynamicBotInfo = (dynamicText = '思考中...') => ({
  content: dynamicText,
  sender: MESSAGE_TYPE.BOT,
  loading: true,
});