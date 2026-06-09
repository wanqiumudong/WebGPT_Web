import request from '../utils/request';
import { fetchType } from './constant';
import { CIRCUIT_BASE_URL } from '../config/endpoints';
import { createStreamRequest } from '../utils/apiUtils';

// Circuit服务直连URL（以当前circuit.py为准）
const CIRCUIT_API_BASE_URL = CIRCUIT_BASE_URL;

/**
 * Circuit Analysis API - 直连版本
 * 支持电路图像分析和网表生成
 */

// Circuit流式聊天API - 直连版本
export const fetchCircuitStreaming = async (data, onChunkReceived, onComplete, onError) => {
  const streamRequest = createStreamRequest({
    baseUrl: CIRCUIT_API_BASE_URL,
    data,
    onChunk: (parsedData) => {
      if (parsedData.chunk && onChunkReceived) {
        onChunkReceived(parsedData.chunk, parsedData);
      }
      if (parsedData.error && onError) {
        onError(parsedData.error);
      }
    },
    onComplete,
    onError: (error) => {
      onError && onError(error.message || 'Circuit流式请求失败');
    }
  });

  return {
    abort: async () => {
      await streamRequest.cancel();
    },
    cancel: async () => {
      await streamRequest.cancel();
    },
    requestId: streamRequest.getRequestId(),
    getRequestId: () => streamRequest.getRequestId()
  };
};

// Circuit图像上传API - 直连版本  
export const uploadCircuitImage = async (file, conversation_id, user_id) => {
  try {
    
    const formData = new FormData();
    formData.append('file', file);
    formData.append('conversation_id', conversation_id || 'default');
    formData.append('user_id', user_id || 'anonymous');
    
    const response = await request({
      baseUrl: CIRCUIT_API_BASE_URL,
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

// Circuit生成请求API（兼容占位）
// 当前后端(circuit.py)无 /generate 路由，请使用 fetchCircuitStreaming(/stream_generate)。
export const fetchCircuitGenerate = async (data) => {
  return {
    error: true,
    message: '当前后端不支持 /generate，请使用 fetchCircuitStreaming(/stream_generate)',
    data
  };
};

// Circuit添加消息API - 直连版本
export const addCircuitMessage = async (messageData) => {
  try {
    
    const response = await request({
      baseUrl: CIRCUIT_API_BASE_URL,
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

// Circuit更新会话API - 直连版本
export const updateCircuitSession = async (sessionData) => {
  try {
    
    const response = await request({
      baseUrl: CIRCUIT_API_BASE_URL,
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
      baseUrl: CIRCUIT_API_BASE_URL,
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
      baseUrl: CIRCUIT_API_BASE_URL,
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
