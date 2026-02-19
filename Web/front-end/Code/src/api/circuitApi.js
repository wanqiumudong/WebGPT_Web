import request from '../utils/request';
import { fetchType } from './constant';

// Circuit负载均衡器URL
const CIRCUIT_LOAD_BALANCER_URL = 'http://10.98.64.22:5103';

/**
 * Circuit Analysis API - 负载均衡版本
 * 支持电路图像分析和网表生成
 */

// Circuit流式聊天API - 负载均衡版本
export const fetchCircuitStreaming = async (data, onChunkReceived, onComplete, onError) => {
  try {
    
    const url = `${CIRCUIT_LOAD_BALANCER_URL}/stream_generate`;
    
    // 创建中止控制器
    const controller = new AbortController();
    const { signal } = controller;
    
    // 存储请求ID,用于中止特定流
    let requestId = null;
    
    const response = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(data),
      signal
    });

    if (!response.ok) {
      throw new Error(`HTTP error! Status: ${response.status}`);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    const processStream = async () => {
      try {
        while (true) {
          const { value, done } = await reader.read();
          
          if (done) {
            if (onComplete) onComplete();
            break;
          }

          const chunk = decoder.decode(value, { stream: true });
          buffer += chunk;

          const lines = buffer.split('\n\n');
          buffer = lines.pop() || '';

          for (const line of lines) {
            if (line.trim() === '') continue;
            
            if (line.startsWith('data: ')) {
              try {
                const jsonStr = line.slice(6).trim();
                if (jsonStr === '[DONE]') {
                  if (onComplete) onComplete();
                  return;
                }
                
                const parsedData = JSON.parse(jsonStr);
                
                // 存储请求ID用于可能的中止操作
                if (parsedData.request_id && !requestId) {
                  requestId = parsedData.request_id;
                }
                
                if (parsedData.chunk && onChunkReceived) {
                  onChunkReceived(parsedData.chunk, parsedData);
                }
                
                if (parsedData.is_complete) {
                  if (onComplete) onComplete();
                  return;
                }
                
                if (parsedData.error) {
                  if (onError) onError(parsedData.error);
                  return;
                }
              } catch (parseError) {
                // JSON解析错误，跳过此行
              }
            }
          }
        }
      } catch (streamError) {
        if (onError) onError(streamError.message);
      }
    };

    processStream();
    
    // 返回中止函数
    return {
      abort: () => {
        controller.abort();
        
        // 如果有请求ID，通知负载均衡器中止
        if (requestId) {
          fetch(`${CIRCUIT_LOAD_BALANCER_URL}/abort_stream`, {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
            },
            body: JSON.stringify({ request_id: requestId }),
          }).catch(err => {});
        }
      },
      requestId
    };
    
  } catch (error) {
    if (onError) onError(error.message || 'Circuit流式请求失败');
    return null;
  }
};

// Circuit图像上传API - 负载均衡版本  
export const uploadCircuitImage = async (file, conversation_id, user_id) => {
  try {
    
    const formData = new FormData();
    formData.append('file', file);
    formData.append('conversation_id', conversation_id || 'default');
    formData.append('user_id', user_id || 'anonymous');
    
    const response = await request({
      baseUrl: CIRCUIT_LOAD_BALANCER_URL,
      url: '/uploadFile',
      data: formData,
      type: 'file',
      method: fetchType.post
    });
    
    return response;
    
  } catch (error) {
    return {
      error: true,
      message: error.message || 'Circuit图像上传失败'
    };
  }
};

// Circuit生成请求API - 负载均衡版本
export const fetchCircuitGenerate = async (data) => {
  try {
    
    const response = await request({
      baseUrl: CIRCUIT_LOAD_BALANCER_URL,
      url: '/generate',
      data: data,
      method: fetchType.post
    });
    
    return response;
    
  } catch (error) {
    return {
      error: true,
      message: error.message || 'Circuit生成请求失败'
    };
  }
};

// Circuit添加消息API - 负载均衡版本
export const addCircuitMessage = async (messageData) => {
  try {
    
    const response = await request({
      baseUrl: CIRCUIT_LOAD_BALANCER_URL,
      url: '/add_message',
      data: messageData,
      method: fetchType.post
    });
    
    return response;
    
  } catch (error) {
    return {
      error: true,
      message: error.message || 'Circuit添加消息失败'
    };
  }
};

// Circuit更新会话API - 负载均衡版本
export const updateCircuitSession = async (sessionData) => {
  try {
    
    const response = await request({
      baseUrl: CIRCUIT_LOAD_BALANCER_URL,
      url: '/update_session',
      data: sessionData,
      method: fetchType.post
    });
    
    return response;
    
  } catch (error) {
    return {
      error: true,
      message: error.message || 'Circuit更新会话失败'
    };
  }
};


// Circuit切换推理模式API
export const switchCircuitMode = async (mode) => {
  try {
    
    const response = await request({
      baseUrl: CIRCUIT_LOAD_BALANCER_URL,
      url: '/switch_mode',
      data: { mode },
      method: fetchType.post
    });
    
    return response;
    
  } catch (error) {
    return {
      error: true,
      message: error.message || 'Circuit模式切换失败'
    };
  }
};

// Circuit获取当前模式API
export const getCurrentCircuitMode = async () => {
  try {
    const response = await request({
      baseUrl: CIRCUIT_LOAD_BALANCER_URL,
      url: '/current_mode',
      method: fetchType.get
    });
    
    return response;
    
  } catch (error) {
    return {
      error: true,
      current_mode: 'unknown',
      available_modes: [],
      message: error.message || '获取Circuit模式失败'
    };
  }
};

// 导出所有Circuit API函数
export default {
  fetchCircuitStreaming,
  uploadCircuitImage,
  fetchCircuitGenerate,
  addCircuitMessage,
  updateCircuitSession,
  switchCircuitMode,
  getCurrentCircuitMode
};