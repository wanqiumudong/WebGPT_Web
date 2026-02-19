import request from '../utils/request';
import { fetchType } from './constant';


// Defect负载均衡器URL
const DEFECT_LOAD_BALANCER_URL = 'http://10.98.64.22:5101';

export const fetchUploadImage = async (data, sessionId) => {
  try {
    const formData = new FormData();
    let username = 'anonymous';
    
    // 处理不同类型的data
    if (data instanceof FormData) {
      for (let [key, value] of data.entries()) {
        formData.append(key, value);
        if (key === 'username') {
          username = value;
        }
      }
    } else if (data.image) {
      formData.append('image', data.image);
      formData.append('user_input', data.user_input || '');
      formData.append('username', data.username || 'anonymous');
      username = data.username || 'anonymous';
    } else {
      for (let key in data) {
        formData.append(key, data[key]);
        if (key === 'username') {
          username = data[key];
        }
      }
    }


    const response = await request({
      baseUrl: DEFECT_LOAD_BALANCER_URL,
      url: '/predict',
      data: formData,
      type: 'file',
      method: fetchType.post,
      headers: {
        'X-Conversation-Id': sessionId || 'default',
        'X-Username': username
      }
    });

    return response;
  } catch(e) {
    return '后端算法还在优化中哦';
  }
};

export const fetchUploadMessage = async(data, sessionId) => {
  try {
    // 直接发送消息字符串，就像备份版本一样
    const message = data.message || data.user_input || '';
    
    const response = await request({
      baseUrl: DEFECT_LOAD_BALANCER_URL,
      url: '/predict',
      data: message,  // 直接发送字符串数据
      method: fetchType.post,
      headers: {
        'X-Conversation-Id': sessionId || 'default',
        'X-Username': data.username || 'anonymous'
      }
    });

    return response.response || response.message;
  } catch(e) {
    return '后端算法还在优化中哦';
  }
};