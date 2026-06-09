import request from '../utils/request';
import { fetchType } from './constant';
import { DEFECT_BASE_URL, SERVICE_HOST } from '../config/endpoints';

// 仅使用 web_demo.py，不经过负载均衡器
const FAB_WEB_DEMO_URL = process.env.REACT_APP_FABGPT_URL || DEFECT_BASE_URL;
// 兼容旧端口（历史上 web_demo.py 常用 2226）
const FAB_WEB_DEMO_LEGACY_URL = `http://${SERVICE_HOST}:2226`;

const normalizeMessage = (response) => {
  if (typeof response === 'string') return response;
  return response?.response || response?.message || response?.content || '';
};

export const fetchUploadImage = async (data, sessionId) => {
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

  // 兼容不同后端字段名：部分服务使用 file，部分使用 image
  if (!formData.get('file') && formData.get('image')) {
    formData.append('file', formData.get('image'));
  }

  const headers = {
    'X-Conversation-Id': sessionId || 'default',
    'X-Username': username
  };

  try {
    return await request({
      baseUrl: FAB_WEB_DEMO_URL,
      url: '/predict',
      data: formData,
      type: 'file',
      method: fetchType.post,
      headers
    });
  } catch (e1) {
    try {
      return await request({
        baseUrl: FAB_WEB_DEMO_URL,
        url: '/uploadImage',
        data: formData,
        type: 'file',
        method: fetchType.post,
        headers
      });
    } catch (e2) {
      // 主端口已失败时，回退历史端口
      if (FAB_WEB_DEMO_URL === FAB_WEB_DEMO_LEGACY_URL) {
        return '后端算法还在优化中哦';
      }
      try {
        return await request({
          baseUrl: FAB_WEB_DEMO_LEGACY_URL,
          url: '/predict',
          data: formData,
          type: 'file',
          method: fetchType.post,
          headers
        });
      } catch (e3) {
        try {
          return await request({
            baseUrl: FAB_WEB_DEMO_LEGACY_URL,
            url: '/uploadImage',
            data: formData,
            type: 'file',
            method: fetchType.post,
            headers
          });
        } catch (e4) {
          return '后端算法还在优化中哦';
        }
      }
    }
  }
};

export const fetchUploadMessage = async(data, sessionId) => {
  // 直接发送消息字符串
  const message = data.message || data.user_input || '';
  const headers = {
    'X-Conversation-Id': sessionId || 'default',
    'X-Username': data.username || 'anonymous'
  };

  try {
    const response = await request({
      baseUrl: FAB_WEB_DEMO_URL,
      url: '/predict',
      data: message,
      method: fetchType.post,
      headers
    });
    return normalizeMessage(response);
  } catch (e1) {
    try {
      const response = await request({
        baseUrl: FAB_WEB_DEMO_URL,
        url: '/uploadMessage',
        data: message,
        method: fetchType.post,
        headers
      });
      return normalizeMessage(response);
    } catch (e2) {
      if (FAB_WEB_DEMO_URL === FAB_WEB_DEMO_LEGACY_URL) {
        return '后端算法还在优化中哦';
      }
      try {
        const response = await request({
          baseUrl: FAB_WEB_DEMO_LEGACY_URL,
          url: '/predict',
          data: message,
          method: fetchType.post,
          headers
        });
        return normalizeMessage(response);
      } catch (e3) {
        try {
          const response = await request({
            baseUrl: FAB_WEB_DEMO_LEGACY_URL,
            url: '/uploadMessage',
            data: message,
            method: fetchType.post,
            headers
          });
          return normalizeMessage(response);
        } catch (e4) {
          return '后端算法还在优化中哦';
        }
      }
    }
  }
};
