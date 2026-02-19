/**
 * 统一的API工具函数
 * 标准化所有模块的API调用格式
 */

/**
 * 模块端口映射 - 实际部署端口配置
 */
export const MODULE_PORTS = {
  CHATBOT: 5003,        // 本地Qwen2聊天服务 (保留在本地)
  FABGPT: 5008,         // Defect检测服务起始端口 (远程A100)
  GUANGKE: 2227,        // 保留原配置
  TCAD: 5002,           // 本地TCAD RAG服务 (保留在本地)
  CIRCUITTHINK: 5007,   // Circuit分析服务 (远程A100)
  RAGMANAGER: 5100,     // RAG负载均衡器端口 (统一入口)
  LITHO: 2229           // 保留原配置
};

/**
 * 负载均衡器端口映射 - 新的端口分配
 */
export const LOAD_BALANCER_PORTS = {
  RAG: 5100,            // RAG负载均衡器 (原5000)
  DEFECT: 5101,         // Defect负载均衡器 (原5002)
  TCAD: 5102,           // TCAD负载均衡器 (原5004)
  CIRCUIT: 5103,        // Circuit负载均衡器 (原5005)
  CHATBOT: 5104         // Chatbot负载均衡器 (原5008)
};

/**
 * 服务器IP配置
 */
export const SERVER_IPS = {
  LOCAL: '10.98.64.22',      // 本地服务器
  REMOTE_A100: '10.98.193.46'  // 远程A100服务器
};

/**
 * A100远程服务配置
 */
export const A100_SERVICES = {
  // Defect服务 - 8个GPU实例
  DEFECT_PORTS: [5008, 5018, 5028, 5038, 5048, 5058, 5068, 5078],
  
  // RAG服务 - 已迁移到负载均衡器，无需直接访问实例
  // RAG_PORTS: [5006, 5016, 5026, 5036, 5046, 5056, 5066, 5076], // 废弃：现在通过负载均衡器访问
  
  // Circuit服务 - 单实例
  CIRCUIT_PORT: 5007
};

/**
 * 构建API基础URL
 * @param {string} service 服务名称 (对应MODULE_PORTS的键)
 * @param {boolean} useLoadBalancing 是否使用负载均衡 (随机选择实例)
 * @returns {string} 完整的API基础URL
 */
export const buildApiUrl = (service, useLoadBalancing = false) => {
  
  // 本地服务直接返回
  if (service === 'CHATBOT' || service === 'TCAD') {
    const port = MODULE_PORTS[service];
    return `http://${SERVER_IPS.LOCAL}:${port}`;
  }
  
  // 远程A100服务
  if (service === 'FABGPT') {
    if (useLoadBalancing) {
      // 随机选择一个Defect实例
      const ports = A100_SERVICES.DEFECT_PORTS;
      const selectedPort = ports[Math.floor(Math.random() * ports.length)];
      return `http://${SERVER_IPS.REMOTE_A100}:${selectedPort}`;
    } else {
      // 使用第一个实例
      return `http://${SERVER_IPS.REMOTE_A100}:${A100_SERVICES.DEFECT_PORTS[0]}`;
    }
  }
  
  if (service === 'RAGMANAGER') {
    // 直接使用RAG负载均衡器，不需要手动负载均衡
    return `http://${SERVER_IPS.LOCAL}:${MODULE_PORTS.RAGMANAGER}`;
  }
  
  if (service === 'CIRCUITTHINK') {
    return `http://${SERVER_IPS.REMOTE_A100}:${A100_SERVICES.CIRCUIT_PORT}`;
  }
  
  // 默认配置
  const port = MODULE_PORTS[service];
  return `http://${SERVER_IPS.LOCAL}:${port}`;
};

/**
 * 获取所有可用的服务实例URL
 * @param {string} service 服务名称
 * @returns {string[]} 所有实例的URL列表
 */
export const getAllServiceUrls = (service) => {
  if (service === 'FABGPT') {
    return A100_SERVICES.DEFECT_PORTS.map(port => 
      `http://${SERVER_IPS.REMOTE_A100}:${port}`
    );
  }
  
  if (service === 'RAGMANAGER') {
    // RAG负载均衡器只有一个统一入口
    return [`http://${SERVER_IPS.LOCAL}:${MODULE_PORTS.RAGMANAGER}`];
  }
  
  // 单实例服务
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
  
  let requestId = null;
  
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
          onComplete && onComplete();
          return;
        }

        const chunk = decoder.decode(value);
        const lines = chunk.split('\n\n');
        
        for (const line of lines) {
          if (line.startsWith('data: ')) {
            try {
              const jsonData = line.substring(6);
              let parsedData;
              
              try {
                parsedData = JSON.parse(jsonData);
              } catch (parseError) {
                // 尝试修复特殊字符
                const fixedData = jsonData.replace(/\\u([0-9a-fA-F]{4})/g, (match, hex) => {
                  try {
                    return String.fromCharCode(parseInt(hex, 16));
                  } catch (e) {
                    return match;
                  }
                });
                
                try {
                  parsedData = JSON.parse(fixedData);
                } catch (secondError) {
                  // 手动解析chunk字段
                  const chunkMatch = jsonData.match(/\"chunk\"\\s*:\\s*\"([^\"\\\\]*(?:\\\\.[^\"\\\\]*)*)\"/);
                  if (chunkMatch && chunkMatch[1]) {
                    parsedData = {
                      chunk: chunkMatch[1].replace(/\\\"/g, '\"').replace(/\\\\/g, '\\'),
                      is_complete: jsonData.includes('\"is_complete\":true'),
                      aborted: jsonData.includes('\"aborted\":true')
                    };
                  } else {
                    continue;
                  }
                }
              }

              // 检查开始标记
              if (parsedData.start_streaming && parsedData.request_id) {
                requestId = parsedData.request_id;
              }

              // 发送数据块
              onChunk && onChunk(parsedData);

              if (parsedData.is_complete) {
                onComplete && onComplete();
                return;
              } else if (parsedData.aborted) {
                onComplete && onComplete();
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

  return {
    cancel: async () => {
      try {
        // 通知后端中止请求
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
        
        // 中止fetch请求
        controller.abort();
        onComplete && onComplete();
        return true;
      } catch (e) {
        console.error('取消请求时出错:', e);
        onComplete && onComplete();
        throw e;
      }
    },
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