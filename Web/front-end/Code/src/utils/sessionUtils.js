/**
 * 统一的会话管理工具函数
 * 所有模块使用相同的会话管理逻辑，确保一致性
 */
import Cookies from 'js-cookie';
import { createRealSessionAfterChat, DEFAULT_SESSION } from '../components/history/history';
import { BACKEND_BASE_URL } from '../config/endpoints';

const API_BASE_URL = BACKEND_BASE_URL;

/**
 * 模块ID映射表
 */
export const MODEL_IDS = {
  CHATBOT: 0,
  FABGPT: 1,
  GUANGKE: 2,
  TCAD: 3,
  CIRCUITTHINK: 5,
  RAGMANAGER: 6
};

/**
 * Cookie键映射表
 */
export const COOKIE_KEYS = {
  [MODEL_IDS.CHATBOT]: 5,
  [MODEL_IDS.FABGPT]: 1,
  [MODEL_IDS.GUANGKE]: 2,
  [MODEL_IDS.TCAD]: 3,
  [MODEL_IDS.CIRCUITTHINK]: 'circuit_5',
  [MODEL_IDS.RAGMANAGER]: 6
};

/**
 * 获取当前会话ID
 * @param {number} modelId 模块ID
 * @returns {string} 会话ID
 */
export const getCurrentSessionId = (modelId) => {
  const cookieKey = COOKIE_KEYS[modelId];
  return Cookies.get(cookieKey);
};

/**
 * 设置会话ID
 * @param {number} modelId 模块ID
 * @param {string} sessionId 会话ID
 */
export const setSessionId = (modelId, sessionId) => {
  const cookieKey = COOKIE_KEYS[modelId];
  Cookies.set(cookieKey, sessionId, { expires: 7 });
};

/**
 * 检查是否为默认会话状态
 * @param {number} modelId 模块ID
 * @returns {boolean} 是否为默认会话
 */
export const isDefaultSessionState = (modelId) => {
  const sessionId = getCurrentSessionId(modelId);
  return !sessionId || sessionId === DEFAULT_SESSION;
};

/**
 * 确保会话存在，如果是默认会话则创建真实会话
 * @param {number} modelId 模块ID
 * @param {string} userMessage 用户消息（用于生成标题）
 * @returns {Promise<string>} 会话ID
 */
export const ensureRealSession = async (modelId, userMessage = '') => {
  let sessionId = getCurrentSessionId(modelId);
  
  if (isDefaultSessionState(modelId)) {
    const newSessionId = await createRealSessionAfterChat(modelId);
    if (newSessionId) {
      sessionId = newSessionId;
      setSessionId(modelId, sessionId);
    } else {
      throw new Error('创建会话失败');
    }
  }
  
  return sessionId;
};

/**
 * 获取下一个消息ID
 * @returns {Promise<number>} 消息ID
 */
export const getNextMessageId = async () => {
  try {
    const response = await fetch(`${API_BASE_URL}/message/list-all`);
    if (response.ok) {
      const allMessages = await response.json();
      return allMessages.length ? Math.max(...allMessages.map(msg => msg.messageId)) + 1 : 1;
    }
  } catch (error) {
    console.error('获取消息ID失败:', error);
  }
  return Date.now(); // 回退方案
};

/**
 * 保存消息到数据库
 * @param {Object} messageData 消息数据
 * @returns {Promise<boolean>} 保存结果
 */
export const saveMessage = async (messageData) => {
  try {
    const response = await fetch(`${API_BASE_URL}/message/add`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(messageData),
    });
    return response.ok;
  } catch (error) {
    console.error('保存消息失败:', error);
    return false;
  }
};

/**
 * 更新会话信息
 * @param {Object} sessionData 会话数据
 * @returns {Promise<boolean>} 更新结果
 */
export const updateSession = async (sessionData) => {
  try {
    const response = await fetch(`${API_BASE_URL}/session/update`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(sessionData),
    });
    
    if (response.ok) {
      // 触发历史列表更新事件
      window.sessionUpdated = Date.now();
      window.dispatchEvent(new Event('sessionUpdated'));
      return true;
    }
    return false;
  } catch (error) {
    console.error('更新会话失败:', error);
    return false;
  }
};

/**
 * 生成会话标题
 * @param {string} userMessage 用户消息
 * @param {string} type 消息类型
 * @returns {string} 标题
 */
export const generateSessionTitle = (userMessage, type = 'text') => {
  // 清理HTML标签
  const cleanContent = userMessage.replace(/<[^>]*>/g, '').trim();
  
  switch (type) {
    case 'image':
      return '图像分析';
    case 'circuit':
      return '电路图理解';
    case 'defect':
      return '缺陷分析';
    case 'lithography':
      return '光刻工艺';
    case 'tcad':
      return 'TCAD仿真';
    case 'rag':
      return '文档问答';
    default:
      if (cleanContent) {
        return cleanContent.slice(0, 8) || '新对话';
      }
      return '新对话';
  }
};

/**
 * 统一的消息处理函数
 * @param {Object} options 配置选项
 * @param {number} options.modelId 模块ID
 * @param {string} options.userMessage 用户消息
 * @param {string} options.userId 用户ID
 * @param {string} options.messageType 消息类型
 * @returns {Promise<Object>} 处理结果
 */
export const handleMessage = async ({
  modelId,
  userMessage,
  userId,
  messageType = 'text'
}) => {
  try {
    // 确保真实会话存在
    const sessionId = await ensureRealSession(modelId, userMessage);
    
    // 获取消息ID
    const messageId = await getNextMessageId();
    
    // 创建用户消息
    const userMessageData = {
      content: userMessage,
      messageId,
      modelId,
      sessionId,
      timestamp: new Date().toISOString(),
      userId,
      userType: 'user',
    };
    
    // 保存用户消息
    await saveMessage(userMessageData);
    
    return {
      success: true,
      sessionId,
      messageId,
      userMessageData
    };
  } catch (error) {
    console.error('处理消息失败:', error);
    return {
      success: false,
      error: error.message
    };
  }
};

/**
 * 清理过期的localStorage数据
 * @param {string} prefix 前缀
 */
export const cleanupLocalStorage = (prefix) => {
  const keys = Object.keys(localStorage);
  keys.forEach(key => {
    if (key.startsWith(prefix)) {
      localStorage.removeItem(key);
    }
  });
};
