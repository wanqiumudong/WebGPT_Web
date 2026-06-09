/**
 * 统一的API工具函数
 * 标准化所有模块的API调用格式
 */

import {
  BACKEND_BASE_URL,
  CHATBOT_BASE_URL,
  CIRCUIT_BASE_URL,
  DEFECT_BASE_URL,
  LITHO_BASE_URL,
  RAG_MANAGER_BASE_URL,
  SERVICE_HOST,
  SERVICE_PORTS,
  TCAD_BASE_URL,
  buildBaseUrl,
} from '../config/endpoints';

/**
 * 模块端口映射 - 实际部署端口配置
 */
export const MODULE_PORTS = {
  CHATBOT: SERVICE_PORTS.CHATBOT,
  FABGPT: SERVICE_PORTS.DEFECT,
  GUANGKE: SERVICE_PORTS.LITHO,
  TCAD: SERVICE_PORTS.TCAD,
  CIRCUITTHINK: SERVICE_PORTS.CIRCUIT,
  RAGMANAGER: SERVICE_PORTS.RAG_MANAGER,
  LITHO: SERVICE_PORTS.LITHO
};

/**
 * 历史兼容字段（当前全部改为本机固定端口）
 */
export const LOAD_BALANCER_PORTS = {
  RAG: SERVICE_PORTS.RAG_MANAGER,
  DEFECT: SERVICE_PORTS.DEFECT,
  TCAD: SERVICE_PORTS.TCAD,
  CIRCUIT: SERVICE_PORTS.CIRCUIT,
  CHATBOT: SERVICE_PORTS.CHATBOT
};

/**
 * 服务器IP配置
 */
export const SERVER_IPS = {
  LOCAL: SERVICE_HOST,
  REMOTE_A100: SERVICE_HOST
};

/**
 * 历史字段兼容
 */
export const A100_SERVICES = {
  DEFECT_PORTS: [SERVICE_PORTS.DEFECT],
  CIRCUIT_PORT: SERVICE_PORTS.CIRCUIT
};

/**
 * 构建API基础URL
 * @param {string} service 服务名称 (对应MODULE_PORTS的键)
 * @param {boolean} useLoadBalancing 是否使用负载均衡 (随机选择实例)
 * @returns {string} 完整的API基础URL
 */
export const buildApiUrl = (service, useLoadBalancing = false) => {
  switch (service) {
    case 'CHATBOT':
      return CHATBOT_BASE_URL;
    case 'FABGPT':
      return DEFECT_BASE_URL;
    case 'GUANGKE':
    case 'LITHO':
      return LITHO_BASE_URL;
    case 'TCAD':
      return TCAD_BASE_URL;
    case 'CIRCUITTHINK':
      return CIRCUIT_BASE_URL;
    case 'RAGMANAGER':
      return RAG_MANAGER_BASE_URL;
    case 'BACKEND':
      return BACKEND_BASE_URL;
    default: {
      const port = MODULE_PORTS[service];
      return buildBaseUrl(port);
    }
  }
};

/**
 * 获取所有可用的服务实例URL
 * @param {string} service 服务名称
 * @returns {string[]} 所有实例的URL列表
 */
export const getAllServiceUrls = (service) => {
  if (service === 'FABGPT') {
    return [DEFECT_BASE_URL];
  }

  if (service === 'RAGMANAGER') {
    return [RAG_MANAGER_BASE_URL];
  }

  return [buildApiUrl(service)];
};

/**
 * 统一的API响应格式
 */
export const createResponse = (success, data = null, error = null) => ({
  success,
  data,
  error,
  timestamp: new Date().toISOString()
});

/**
 * 统一的错误处理
 */
export const handleApiError = (error, context = '') => {
  console.error(`API错误 [${context}]:`, error);
  return createResponse(false, null, error.message || '请求失败');
};

/**
 * 统一的流式API调用
 * @param {Object} options 配置选项
 * @param {string} options.baseUrl 基础URL
 * @param {Object} options.data 请求数据
 * @param {Function} options.onChunk 处理数据块的回调
 * @param {Function} options.onComplete 完成回调
 * @param {Function} options.onError 错误回调
 * @returns {Object} 控制对象
 */
export const createStreamRequest = ({
  baseUrl,
  data,
  onChunk,
  onComplete,
  onError
}) => {
  const controller = new AbortController();
  const { signal } = controller;
  let buffer = '';
  let requestId = null;
  let finished = false;

  const finish = () => {
    if (finished) {
      return;
    }
    finished = true;
    onComplete && onComplete();
  };

  const parseStreamData = (jsonData) => {
    try {
      return JSON.parse(jsonData);
    } catch (parseError) {
      const fixedData = jsonData.replace(/\\u([0-9a-fA-F]{4})/g, (match, hex) => {
        try {
          return String.fromCharCode(parseInt(hex, 16));
        } catch (error) {
          return match;
        }
      });

      try {
        return JSON.parse(fixedData);
      } catch (secondError) {
        const chunkMatch = jsonData.match(/"chunk"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"/);
        if (chunkMatch && chunkMatch[1]) {
          return {
            chunk: chunkMatch[1].replace(/\\"/g, '"').replace(/\\\\/g, '\\'),
            is_complete: jsonData.includes('"is_complete":true'),
            aborted: jsonData.includes('"aborted":true'),
          };
        }
        throw secondError;
      }
    }
  };

  const startStream = async () => {
    try {
      const url = `${baseUrl}/stream_generate`;
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

      const processStream = async ({ done, value }) => {
        if (done) {
          finish();
          return;
        }

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            try {
              const jsonData = line.substring(6);
              const parsedData = parseStreamData(jsonData);

              // 检查开始标记
              if (parsedData.start_streaming && parsedData.request_id) {
                requestId = parsedData.request_id;
              }

              // 发送数据块
              onChunk && onChunk(parsedData);

              if (parsedData.is_complete) {
                finish();
                return;
              } else if (parsedData.aborted) {
                finish();
                return;
              }
            } catch (e) {
              console.error('处理SSE数据出错:', e);
            }
          }
        }
        
        // 继续读取
        try {
          const result = await reader.read();
          return processStream(result);
        } catch (error) {
          if (error.name === 'AbortError') {
          } else {
            throw error;
          }
        }
      };

      reader.read().then(processStream);
    } catch (error) {
      if (error.name === 'AbortError') {
      } else {
        console.error('流式请求出错:', error);
        onError && onError(error);
      }
    }
  };

  startStream();

  const cancel = async () => {
    try {
      if (requestId) {
        try {
          await fetch(`${baseUrl}/abort_stream`, {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json'
            },
            body: JSON.stringify({ request_id: requestId })
          });
        } catch (e) {
          console.error('发送中止请求失败:', e);
        }
      }

      controller.abort();
      return true;
    } catch (e) {
      console.error('取消请求时出错:', e);
      throw e;
    }
  };

  return {
    cancel,
    abort: cancel,
    getRequestId: () => requestId
  };
};

/**
 * 统一的文件上传函数
 * @param {Object} options 配置选项
 * @param {string} options.baseUrl 基础URL
 * @param {File} options.file 文件
 * @param {string} options.conversationId 会话ID
 * @param {string} options.userId 用户ID
 * @param {Function} options.onProgress 进度回调
 * @returns {Promise<Object>} 上传结果
 */
export const uploadFile = async ({
  baseUrl,
  file,
  conversationId,
  userId,
  onProgress
}) => {
  try {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('conversation_id', conversationId);
    formData.append('user_id', userId);

    const response = await fetch(`${baseUrl}/uploadFile`, {
      method: 'POST',
      body: formData,
      onUploadProgress: (progressEvent) => {
        if (onProgress && progressEvent.lengthComputable) {
          const progress = (progressEvent.loaded / progressEvent.total) * 100;
          onProgress(progress);
        }
      }
    });

    if (response.ok) {
      const result = await response.json();
      return createResponse(true, result);
    } else {
      throw new Error(`上传失败: ${response.status}`);
    }
  } catch (error) {
    return handleApiError(error, '文件上传');
  }
};

/**
 * 统一的消息发送函数
 * @param {Object} options 配置选项
 * @param {string} options.baseUrl 基础URL
 * @param {string} options.message 消息内容
 * @param {string} options.userId 用户ID
 * @param {string} options.conversationId 会话ID
 * @param {string} options.configId RAG配置ID（可选）
 * @returns {Promise<Object>} 发送结果
 */
export const sendMessage = async ({
  baseUrl,
  message,
  userId,
  conversationId,
  configId = 'default'
}) => {
  try {
    const response = await fetch(`${baseUrl}/generate`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        message,
        user_id: userId,
        conversation_id: conversationId,
        config_id: configId
      })
    });

    if (response.ok) {
      const result = await response.json();
      return createResponse(true, result);
    } else {
      throw new Error(`发送失败: ${response.status}`);
    }
  } catch (error) {
    return handleApiError(error, '消息发送');
  }
};

/**
 * 删除上传的文件
 * @param {Object} options 配置选项
 * @param {string} options.baseUrl 基础URL
 * @param {string} options.fileName 文件名
 * @param {string} options.conversationId 会话ID
 * @returns {Promise<Object>} 删除结果
 */
export const deleteUploadedFile = async ({
  baseUrl,
  fileName,
  conversationId
}) => {
  try {
    const response = await fetch(`${baseUrl}/deleteFile`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        file_name: fileName,
        conversation_id: conversationId
      })
    });

    if (response.ok) {
      const result = await response.json();
      return createResponse(true, result);
    } else {
      throw new Error(`删除失败: ${response.status}`);
    }
  } catch (error) {
    return handleApiError(error, '文件删除');
  }
};
