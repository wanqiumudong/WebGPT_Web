import { message } from 'antd';
import Cookies from 'js-cookie';

// const BASE_URL = 'http://10.98.193.23:2226';
// target: 'http://10.98.193.23:5002',

const request = async (options) => {
  const { url, baseUrl, method, data, headers = {}, type = '' } = options;
  const username = Cookies.get('user');

  let newData = undefined;
  const isFile = type === 'file';

  if (isFile) {
    newData = data;
  } else {
    newData = typeof data === 'object' ? JSON.stringify(data) : data;
  }

  try {
    const response = await fetch(`${baseUrl}${url}`, {
      method,
      body: newData,
      headers: {
        'X-Username': username,
        ...headers,
      },
    });

    // 判断响应是否成功
    if (response.ok) {
      const contentType = response.headers.get('content-type');
      
      if (isFile) {
        // 对于文件上传，先检查是否是JSON响应
        if (contentType && contentType.includes('application/json')) {
          // 新的JSON格式响应
          return await response.json();
        } else {
          // 传统的blob响应
          return await response.blob();
        }
      } else if (response.headers.get('content-length') === '0' || !response.body) {
        // 如果响应为空，返回默认值（空对象或其他默认值）
        return response.status;
      } else {
        // 尝试解析 JSON，如果失败则返回默认值
        try {
          return await response.json();
        } catch (error) {
          return response.status;
        }
      }
    } else {
      message.error(`请求出错了: 状态码 ${response.status}`);
      return Promise.reject(new Error(`HTTP error! Status: ${response.status}`));
    }
  } catch (e) {
    // 捕获网络错误或其他异常
    message.error('请求出错了_2');
    return Promise.reject(e);
  }
};





export function setLocalStorageWithExpiration(key, value, expirationMinutes) {
  const expirationMS = expirationMinutes * 60 * 1000;
  const record = { value: value, expiration: new Date().getTime() + expirationMS };
  localStorage.setItem(key, JSON.stringify(record));
}

export function getLocalStorageWithExpiration(key) {
  const record = JSON.parse(localStorage.getItem(key));
  if (!record) {
    return null;
  }
  if (new Date().getTime() > record.expiration) {
    localStorage.removeItem(key);
    return null;
  }
  return record.value;
}




export default request;
