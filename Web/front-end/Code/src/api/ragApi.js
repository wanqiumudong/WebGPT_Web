/**
 * RAG API - A100远程服务器配置
 * 直接连接到A100服务器上的RAG服务实例
 */

import { buildApiUrl } from '../utils/apiUtils';

// A100 RAG服务配置
const getRAGServiceUrl = (useLoadBalancing = false) => {
  return buildApiUrl('RAGMANAGER', useLoadBalancing);
};

/**
 * 通用RAG API请求函数
 * @param {string} endpoint - API端点路径
 * @param {Object} options - 请求选项
 * @param {boolean} useLoadBalancing - 是否使用负载均衡
 */
const ragRequest = async (endpoint, options = {}, useLoadBalancing = false) => {
  const url = `${getRAGServiceUrl(useLoadBalancing)}${endpoint}`;
  const timeout = options.timeout || 30000; // 默认30秒超时
  
  const defaultOptions = {
    headers: {
      'Content-Type': 'application/json',
      ...options.headers
    }
  };
  
  // 合并选项（排除timeout，因为fetch不支持）
  const { timeout: _, ...requestOptions } = { ...defaultOptions, ...options };
  
  
  // 创建AbortController用于超时控制
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeout);
  
  try {
    const response = await fetch(url, {
      ...requestOptions,
      signal: controller.signal
    });
    
    clearTimeout(timeoutId); // 清除超时定时器
    
    if (!response.ok) {
      throw new Error(`A100 RAG HTTP ${response.status}: ${response.statusText}`);
    }
    
    // 根据响应类型处理
    const contentType = response.headers.get('content-type');
    if (contentType && contentType.includes('application/json')) {
      return await response.json();
    } else {
      return await response.text();
    }
    
  } catch (error) {
    clearTimeout(timeoutId); // 确保清除超时定时器
    
    if (error.name === 'AbortError') {
      console.error(`A100 RAG API请求超时 ${endpoint}: ${timeout}ms`);
      throw new Error(`请求超时: ${timeout/1000}秒`);
    }
    
    console.error(`A100 RAG API请求失败 ${endpoint}:`, error);
    throw error;
  }
};

/**
 * 获取RAG配置列表
 */
export const getRagConfigurations = async (userId = 'anonymous') => {
  return ragRequest(`/get_rag_configurations?user_id=${userId}`, {
    method: 'GET'
  });
};

/**
 * 创建RAG配置
 */
export const createRagConfiguration = async (userId, name) => {
  return ragRequest('/create_rag_configuration', {
    method: 'POST',
    body: JSON.stringify({
      user_id: userId,
      name: name
    })
  });
};

/**
 * 设置活跃配置
 */
export const setActiveConfiguration = async (userId, configId) => {
  return ragRequest('/set_active_configuration', {
    method: 'POST',
    body: JSON.stringify({
      user_id: userId,
      config_id: configId
    })
  });
};

/**
 * 删除RAG配置
 */
export const deleteRagConfiguration = async (userId, configId) => {
  return ragRequest('/delete_rag_configuration', {
    method: 'POST',
    body: JSON.stringify({
      user_id: userId,
      config_id: configId,
      force_delete: true
    })
  });
};

/**
 * 获取RAG文档列表
 */
export const getRagDocuments = async (userId, configId, forceRefresh = false) => {
  const params = new URLSearchParams({
    user_id: userId,
    config_id: configId
  });
  
  if (forceRefresh) {
    params.append('force_refresh', 'true');
  }
  
  return ragRequest(`/get_rag_documents?${params.toString()}`, {
    method: 'GET',
    headers: {
      'Cache-Control': 'no-cache',
      'Pragma': 'no-cache'
    }
  });
};

/**
 * 上传RAG文档
 */
export const uploadRagDocument = async (formData) => {
  // 对于文件上传，不设置Content-Type让浏览器自动设置
  return ragRequest('/upload_rag_document', {
    method: 'POST',
    body: formData,
    headers: {} // 清空headers让浏览器设置multipart/form-data
  });
};

/**
 * 删除RAG文档
 */
export const deleteRagDocument = async (userId, configId, docId) => {
  return ragRequest('/delete_rag_document', {
    method: 'POST',
    body: JSON.stringify({
      user_id: userId,
      config_id: configId,
      doc_id: docId,
      physical_delete: true
    })
  });
};

/**
 * 获取相关上下文
 */
export const getRelevantContext = async (userId, configId, query, topK = 5) => {
  return ragRequest('/get_relevant_context', {
    method: 'POST',
    body: JSON.stringify({
      user_id: userId,
      config_id: configId,
      query: query,
      top_k: topK
    })
  });
};

/**
 * 检查处理进度
 */
export const checkProcessingProgress = async (userId, taskId = null) => {
  const params = new URLSearchParams({ user_id: userId });
  if (taskId) {
    params.append('task_id', taskId);
  }
  
  return ragRequest(`/check_processing_progress?${params.toString()}`, {
    method: 'GET',
    headers: {
      'Cache-Control': 'no-cache',
      'Pragma': 'no-cache'
    }
  });
};

/**
 * 获取用户任务
 */
export const getUserTasks = async (userId) => {
  return ragRequest(`/get_user_tasks?user_id=${userId}`, {
    method: 'GET',
    headers: {
      'Cache-Control': 'no-cache, no-store, must-revalidate',
      'Pragma': 'no-cache',
      'Expires': '0'
    }
  });
};

/**
 * 保存用户会话状态
 */
export const saveUserSessionState = async (userId, configId, documents, stats) => {
  return ragRequest('/save_user_session_state', {
    method: 'POST',
    body: JSON.stringify({
      user_id: userId,
      config_id: configId,
      documents: documents,
      stats: stats
    })
  });
};

/**
 * 获取用户会话状态
 */
export const getUserSessionState = async (userId, configId) => {
  return ragRequest(`/get_user_session_state?user_id=${userId}&config_id=${configId}`, {
    method: 'GET',
    headers: {
      'Cache-Control': 'no-cache'
    }
  });
};

/**
 * 获取负载均衡器集群状态
 */
export const getClusterStatus = async () => {
  return ragRequest('/cluster/status', {
    method: 'GET'
  });
};

/**
 * 检查负载均衡器健康状态
 */
export const checkLoadBalancerHealth = async () => {
  return ragRequest('/health', {
    method: 'GET'
  });
};

export default {
  getRagConfigurations,
  createRagConfiguration,
  setActiveConfiguration,
  deleteRagConfiguration,
  getRagDocuments,
  uploadRagDocument,
  deleteRagDocument,
  getRelevantContext,
  checkProcessingProgress,
  getUserTasks,
  saveUserSessionState,
  getUserSessionState,
  getClusterStatus,
  checkLoadBalancerHealth
};