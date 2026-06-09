// RagManager/index.jsx
import React, { useState, useEffect, useRef, useCallback } from 'react';
import {  
  Upload, Button, Table, Space, message, Card, Statistic, Modal,  
  Tooltip, Typography, Progress, Select, Input, Tag
} from 'antd';
import {  
  UploadOutlined, ReloadOutlined, DeleteOutlined, CodeOutlined,
  FileTextOutlined, PlusOutlined, FileImageOutlined, FileTextTwoTone
} from '@ant-design/icons';
import { useSelector } from 'react-redux';
import Cookies from 'js-cookie';
import './index.css';
import { buildBaseUrl } from '../../config/endpoints';
import { resolveCurrentUserId } from '../../utils/userIdentity';

const { Paragraph, Text } = Typography;
const { Option } = Select;

const RagManager = ({ port = 5106, userId }) => {
  // 在组件顶层获取Redux状态
  const reduxUsername = useSelector(state => state.UserState?.username);
  const [currentUserId] = useState(() =>
    resolveCurrentUserId({ preferredUserId: userId, preferredUsername: reduxUsername || Cookies.get('user') })
  );
  const ragBaseUrl = buildBaseUrl(port);
  // 基本状态
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [documents, setDocuments] = useState([]);
  const [milvusStatus, setMilvusStatus] = useState({});
  const [stats, setStats] = useState({ documents_count: 0, total_chunks: 0 });
  const [totalPageCount, setTotalPageCount] = useState(0);
  const [modalVisible, setModalVisible] = useState(false);
  const [currentDocument, setCurrentDocument] = useState(null);
  
  // 知识库配置状态
  const [configurations, setConfigurations] = useState([
    { id: 'default', name: '默认知识库', folder: './knowledge_base/default', active: true }
  ]);
  const [currentConfig, setCurrentConfig] = useState('default');
  const [newConfigModalVisible, setNewConfigModalVisible] = useState(false);
  const [newConfigName, setNewConfigName] = useState('');
  
  // 处理进度状态
  const [processing, setProcessing] = useState(false);
  const [processingProgress, setProcessingProgress] = useState(0);
  const [processingFile, setProcessingFile] = useState('');
  const [processingInfo, setProcessingInfo] = useState(null);
  const [processedCompletionIds, setProcessedCompletionIds] = useState({});
  
  // 操作状态
  const [switchingConfig, setSwitchingConfig] = useState(false);
  const [creatingConfig, setCreatingConfig] = useState(false);
  const [deletingConfig, setDeletingConfig] = useState(false);

  const initializingRef = useRef(false);
  const [isInitialized, setIsInitialized] = useState(false); 
  
  // 添加 refs 以跟踪最新状态
  const statsRef = useRef(stats);
  const totalPageCountRef = useRef(totalPageCount);
  const documentsRef = useRef(documents);
  const processingRef = useRef(processing);
  
  const refreshTimeoutRef = useRef(null);
  const [lastConfigId, setLastConfigId] = useState('');

  // 页面卸载时保存会话状态
  useEffect(() => {
    const handleBeforeUnload = () => {
      if (documents.length > 0 || stats.documents_count > 0) {
        try {
          localStorage.setItem('rag_session_documents', JSON.stringify(documents));
          localStorage.setItem('rag_session_stats', JSON.stringify({
            ...stats,
            total_pages_count: totalPageCount
          }));
        } catch (e) {
          console.error('本地存储保存失败:', e);
        }
      }
    };
    
    window.addEventListener('beforeunload', handleBeforeUnload);
    return () => window.removeEventListener('beforeunload', handleBeforeUnload);
  }, [documents, stats, totalPageCount]);
  
  // 更新 refs 当状态变化时
  useEffect(() => { statsRef.current = stats; }, [stats]);
  useEffect(() => { totalPageCountRef.current = totalPageCount; }, [totalPageCount]);
  useEffect(() => { documentsRef.current = documents; }, [documents]);
  useEffect(() => { processingRef.current = processing; }, [processing]);

  // 保存会话状态
  const saveSessionState = async () => {
    if (documents.length === 0 && stats.documents_count === 0) return;
    
    try {
      const response = await fetch(`${ragBaseUrl}/save_user_session_state`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          user_id: currentUserId,
          config_id: currentConfig,
          documents: documents,
          stats: {
            documents_count: statsRef.current.documents_count,
            total_chunks: statsRef.current.total_chunks,
            total_pages_count: totalPageCountRef.current
          }
        })
      });
      
      if (response.ok) {
        // 会话状态已保存到服务器
      }
    } catch (error) {
      console.error('保存会话状态失败:', error);
    }
  };

  // 恢复会话状态
  const restoreSessionState = async (configId = currentConfig) => {
    try {
      const response = await fetch(`${ragBaseUrl}/get_user_session_state?user_id=${currentUserId}&config_id=${configId}`, {
        signal: AbortSignal.timeout(5000),
        headers: { 'Cache-Control': 'no-cache' }
      });
      
      if (response.ok) {
        const data = await response.json();
        
        if (data.has_state && data.documents && data.documents.length > 0) {
          setDocuments(data.documents);
          documentsRef.current = data.documents;
          
          if (data.stats) {
            setStats({
              documents_count: data.stats.documents_count || 0,
              total_chunks: data.stats.total_chunks || 0
            });
            statsRef.current = {
              documents_count: data.stats.documents_count || 0,
              total_chunks: data.stats.total_chunks || 0
            };
            
            setTotalPageCount(data.stats.total_pages_count || 0);
            totalPageCountRef.current = data.stats.total_pages_count || 0;
          }
          
          return true;
        }
      }
      
      return false;
      
    } catch (error) {
      console.error('恢复会话状态失败:', error);
      return false;
    }
  };
  
  // 初始加载
  useEffect(() => {
    if (currentUserId && !initializingRef.current) {
      let isMounted = true;
      initializingRef.current = true;
      
      (async () => {
        try {
          const activeConfigId = await fetchConfigurations();
          const targetConfigId = activeConfigId || currentConfig;
          const sessionRestored = await restoreSessionState(targetConfigId);
          await fetchUserTasks(targetConfigId);
          
          if (isMounted && !sessionRestored) {
            await fetchRagDocuments(false);
          }
          
          setIsInitialized(true);
        } finally {
          initializingRef.current = false;
        }
      })();
      
      return () => {
        isMounted = false;
        if (refreshTimeoutRef.current) {
          clearTimeout(refreshTimeoutRef.current);
          refreshTimeoutRef.current = null;
        }
      };
    }
  }, [currentUserId]);
  
  // 当前配置变更时获取文档
  useEffect(() => {
    if (!isInitialized || !currentConfig) return;
    
    const timeoutId = setTimeout(() => {
      fetchUserTasks(currentConfig);
      if (!processingRef.current) {
        fetchRagDocuments(false);
      }
    }, 500);
    
    return () => clearTimeout(timeoutId);
  }, [currentConfig, isInitialized]);
  
  // localStorage任务管理
  const saveTaskToLocalStorage = (taskId, fileName, configId) => {
    try {
      if (!taskId) return false;
      
      let normalizedTaskId = taskId;
      if (typeof taskId === 'string' && /^\d+$/.test(taskId)) {
        normalizedTaskId = parseInt(taskId, 10);
      }
      
      const taskInfo = {
        task_id: normalizedTaskId, 
        doc_id: normalizedTaskId,
        original_task_id: taskId,
        file_name: fileName,
        config_id: configId,
        timestamp: Date.now(),
        user_id: currentUserId,
        refresh_count: 0
      };
      
      localStorage.setItem('currentRagTask', JSON.stringify(taskInfo));
      return true;
    } catch (error) {
      console.error('保存任务到localStorage失败:', error);
      return false;
    }
  };
  
  const getTaskFromLocalStorage = () => {
    try {
      const taskInfoStr = localStorage.getItem('currentRagTask');
      if (!taskInfoStr) return null;
      
      const taskInfo = JSON.parse(taskInfoStr);
      
      if (!taskInfo.task_id) {
        localStorage.removeItem('currentRagTask');
        return null;
      }
      
      // 验证任务是否过期(2小时)
      if (Date.now() - (taskInfo.timestamp || 0) > 7200000) {
        localStorage.removeItem('currentRagTask');
        return null;
      }
      
      // 验证用户ID匹配
      if (taskInfo.user_id !== currentUserId) {
        return null;
      }
      
      return {
        ...taskInfo,
        task_id: typeof taskInfo.task_id === 'string' && /^\d+$/.test(taskInfo.task_id)
           ? parseInt(taskInfo.task_id, 10)
           : taskInfo.task_id
      };
    } catch (error) {
      console.error('从localStorage恢复任务失败:', error);
      localStorage.removeItem('currentRagTask');
      return null;
    }
  };
  
  const clearTaskFromLocalStorage = () => {
    localStorage.removeItem('currentRagTask');
  };
  
  // 从后端获取知识库配置
  const fetchConfigurations = async () => {
    try {
      const url = `${ragBaseUrl}/get_rag_configurations?user_id=${currentUserId}`;
      
      const response = await fetch(url);
      if (response.ok) {
        const data = await response.json();
        
        // 防御性检查：确保configurations是数组
        const configs = Array.isArray(data.configurations) ? data.configurations : [];
        if (!Array.isArray(data.configurations)) {
          console.error('⚠️ 后端返回的configurations不是数组:', data.configurations);
        }
        
        setConfigurations(configs);
        const activeConfig = configs.find(c => c.active) || configs[0];
        if (activeConfig && activeConfig.id !== currentConfig) {
          setCurrentConfig(activeConfig.id);
        }
        return activeConfig?.id || null;
      } else {
        console.error('获取配置失败, HTTP状态:', response.status);
        const errorText = await response.text();
        console.error('错误详情:', errorText);
      }
    } catch (error) {
      console.error('获取知识库配置错误:', error);
      message.error('获取知识库配置失败');
    }
    return null;
  };
  
  // 验证任务是否存在并恢复
  const checkTaskExists = async (taskId, fileName) => {
    if (!taskId) {
      clearTaskFromLocalStorage();
      return false;
    }
    
    const normalizedTaskId = typeof taskId === 'string' && taskId.match(/^\d+$/) ? parseInt(taskId, 10) : taskId;
    
    try {
      const response = await fetch(`${ragBaseUrl}/check_processing_progress?task_id=${normalizedTaskId}&user_id=${currentUserId}`, {
        signal: AbortSignal.timeout(8000),
        headers: { 'Cache-Control': 'no-cache' }
      });
      
      if (response.ok) {
        const data = await response.json();
        
        if (data.status === 'processing') {
          const normalizedTask = {
            task_id: taskId,
            file_name: fileName || data.file_name || '未知文件',
            progress: data.progress || 0,
            current_step: data.current_step || '',
            current_page: data.current_page || 0,
            total_pages: data.total_pages || 0,
            processed_pages: data.processed_pages || 0
          };
          
          return restoreProcessingTask(normalizedTask);
        } else if (data.status === 'completed') {
          clearTaskFromLocalStorage();
          setTimeout(() => fetchRagDocuments(true), 1000);
          return true;
        } else {
          clearTaskFromLocalStorage();
          return false;
        }
      } else {
        clearTaskFromLocalStorage();
        return false;
      }
    } catch (error) {
      console.error(`验证任务${taskId}时出错:`, error);
      clearTaskFromLocalStorage();
      return false;
    }
  };
  
  // 恢复处理任务的状态
  const restoreProcessingTask = (task) => {
    if (!task) return false;
    
    const taskId = task.doc_id || task.task_id || task.taskId || task.id;
    if (!taskId) {
      console.error('无法恢复任务: 缺少任何可用的ID字段', task);
      return false;
    }
    
    const fileName = task.file_name || task.fileName || task.original_name || '未知文件';
    
    setProcessing(true);
    setProcessingFile(fileName);
    
    const progress = task.progress || 0;
    setProcessingProgress(progress);
    
    setProcessingInfo({
      task_id: taskId,
      doc_id: task.doc_id || taskId,
      current_step: task.current_step || task.currentStep || '',
      current_page: task.current_page || task.currentPage || 0,
      total_pages: task.total_pages || task.totalPages || 0,
      processed_pages: task.processed_pages || task.processedPages || 0
    });
    
    // 开始跟踪任务进度
    trackProcessingProgress(taskId, fileName, true, progress);
    
    setTimeout(() => restoreSessionState(), 2000);
    
    return true;
  };
  
  // 获取用户任务
  const fetchUserTasks = async (configId = currentConfig) => {
    if (!currentUserId) return;
    
    try {
      const response = await fetch(`${ragBaseUrl}/get_user_tasks?user_id=${currentUserId}&config_id=${configId}`);
      
      if (response.ok) {
        const data = await response.json();

        const tasks = Array.isArray(data.tasks) ? data.tasks : [];
        const processingTask = tasks
          .filter(task =>
            task.status === 'processing' &&
            (!task.config_id || String(task.config_id) === String(configId))
          )
          .sort((a, b) => (b.start_time || 0) - (a.start_time || 0))[0];
        if (processingTask) {
          const taskId = processingTask.doc_id || processingTask.task_id || processingTask.taskId || processingTask.id;
          
          if (taskId) {
            const fileName = processingTask.file_name || processingTask.fileName || processingTask.original_name || '未知文件';
            saveTaskToLocalStorage(taskId, fileName, configId);
            
            const normalizedTask = {
              task_id: taskId,
              doc_id: processingTask.doc_id,
              file_name: fileName,
              progress: processingTask.progress || 0,
              current_step: processingTask.current_step || processingTask.currentStep,
              current_page: processingTask.current_page || processingTask.currentPage,
              total_pages: processingTask.total_pages || processingTask.totalPages,
              processed_pages: processingTask.processed_pages || processingTask.processedPages
            };
            
            restoreProcessingTask(normalizedTask);
          }
        } else {
          const savedTask = getTaskFromLocalStorage();
          if (savedTask && savedTask.task_id && String(savedTask.config_id) === String(configId)) {
            checkTaskExists(savedTask.task_id, savedTask.file_name);
          }
        }
      } else {
        const savedTask = getTaskFromLocalStorage();
        if (savedTask && savedTask.task_id && String(savedTask.config_id) === String(configId)) {
          checkTaskExists(savedTask.task_id, savedTask.file_name);
        }
      }
    } catch (error) {
      console.error('获取用户任务错误:', error);
      
      const savedTask = getTaskFromLocalStorage();
      if (savedTask && savedTask.task_id && String(savedTask.config_id) === String(configId)) {
        checkTaskExists(savedTask.task_id, savedTask.file_name);
      }
    }
  };
  
  // 优化的文档获取函数
  const fetchRagDocuments = async (forceRefresh = false) => {
    setLoading(true);
    
    const requestUrl = `${ragBaseUrl}/get_rag_documents`;
    const searchParams = new URLSearchParams();
    searchParams.append('config_id', currentConfig);
    searchParams.append('user_id', currentUserId);  // 添加用户ID
    if (forceRefresh) {
      searchParams.append('force_refresh', 'true');
    }
    
    const loadingKey = 'loadingDocuments';
    if (forceRefresh) {
      message.loading({ content: '正在刷新文档列表...', key: loadingKey });
    }
    
    try {
      const timeoutValue = processing ? 40000 : 20000;
      
      const response = await fetch(`${requestUrl}?${searchParams.toString()}`, {
        signal: AbortSignal.timeout(timeoutValue),
        headers: {
          'Cache-Control': 'no-cache',
          'Pragma': 'no-cache'
        }
      });
      
      if (response.ok) {
        const responseData = await response.json();
        
        const filteredDocs = Array.isArray(responseData.documents) ?
          responseData.documents.filter(doc =>
            !doc.deleted && (doc.file_exists !== false || doc.status !== 'missing')
          ) : [];

        const serverDocsCount = responseData.documents_count || 0;
        const serverPagesCount = responseData.total_pages_count || 0;

        // 更新引用值
        documentsRef.current = filteredDocs;
        statsRef.current = {
          documents_count: serverDocsCount,
          total_chunks: responseData.total_chunks || 0
        };
        totalPageCountRef.current = serverPagesCount;
        
        // 检查是否是不同知识库配置的请求
        const isConfigChanged = currentConfig !== lastConfigId;
        if (isConfigChanged) {
          setLastConfigId(currentConfig);
        }

        // 更新状态
        setDocuments(prevDocs => {
          if (isConfigChanged || forceRefresh || prevDocs.length !== filteredDocs.length || processingRef.current) {
            return filteredDocs;
          }
          const prevDocIds = prevDocs.map(doc => doc.doc_id).sort();
          const currentDocIds = filteredDocs.map(doc => doc.doc_id).sort();
          if (JSON.stringify(prevDocIds) !== JSON.stringify(currentDocIds)) {
            return filteredDocs;
          }
          return prevDocs;
        });
                    
        setStats(prevStats => {
          if (prevStats.documents_count !== serverDocsCount ||
              prevStats.total_chunks !== (responseData.total_chunks || 0) ||
              processingRef.current) {
            return {
              documents_count: serverDocsCount,
              total_chunks: responseData.total_chunks || 0
            };
          }
          return prevStats;
        });
        
        setTotalPageCount(prevCount => {
          if (prevCount !== serverPagesCount || processingRef.current) {
            return serverPagesCount;
          }
          return prevCount;
        });
        
        setMilvusStatus({
          ...(responseData.milvus_status || {}),
          config_id: currentConfig
        });
        
        if (forceRefresh) {
          if (filteredDocs.length > 0) {
            message.success({
               content: `成功获取${filteredDocs.length}个文档,总页数${serverPagesCount}页`,
              key: loadingKey
             });
          } else {
            message.warning({
               content: '没有找到文档,请检查知识库配置',
              key: loadingKey
             });
          }
        }
        
        setTimeout(() => saveSessionState(), 500);
      } else {
        const errorData = await response.json();
        message.error(`刷新失败: ${errorData.error || `HTTP错误 ${response.status}`}`);
        setTimeout(() => restoreSessionState(), 500);
      }
    } catch (error) {
      console.error('获取知识库文档列表错误:', error);
      
      if (error.name === 'AbortError') {
        message.warning('请求超时,尝试恢复本地数据');
        
        if (documents.length === 0 && statsRef.current.documents_count === 0) {
          setTimeout(() => restoreSessionState(), 500);
        }
      } else {
        message.error(`刷新失败: ${error.message || '未知错误'}`);
      }
    } finally {
      setLoading(false);
    }
  };
  
  // 重置处理状态的函数
  const resetProcessingState = () => {
    setProcessing(false);
    setProcessingProgress(0);
    setProcessingFile('');
    setProcessingInfo(null);
    clearTaskFromLocalStorage();
  };
  
  // 跟踪处理进度
  const trackProcessingProgress = async (taskId, fileName, isResuming = false, initialProgress = 0) => {
    let retryCount = 0;
    const maxRetries = 20;
    
    let lastProgressValue = initialProgress > 0 ? initialProgress : (processingProgress || 0);
    
    if (!taskId) {
      console.error('无法获取有效的ID,无法跟踪进度');
      return;
    }
    
    const docId = taskId;
    let statsUpdateCounter = 0;
    
    const checkProgressByPolling = async () => {
      if (!processingRef.current && !isResuming) {
        return;
      }
      
      try {
        const timestamp = new Date().getTime();
        
        const response = await fetch(`${ragBaseUrl}/get_user_tasks?user_id=${currentUserId}&config_id=${currentConfig}&_t=${timestamp}`, {
          headers: {
            'Cache-Control': 'no-cache, no-store, must-revalidate',
            'Pragma': 'no-cache',
            'Expires': '0'
          }
        });
        
        if (response.ok) {
          const data = await response.json();
          
          const matchingTask = data.tasks && data.tasks.find(task => {
            return (
              task.doc_id === docId || 
              String(task.doc_id) === String(docId) ||
              task.task_id === docId || 
              String(task.task_id) === String(docId) ||
              (task.file_name === fileName && task.status === 'processing')
            );
          });
          
          if (matchingTask) {
            if (matchingTask.status === 'completed') {
              setProcessingProgress(100);
              message.success(`${fileName} 处理完成`);
              
              const completionId = matchingTask.completion_id || `${docId}_complete`;
              
              if (!processedCompletionIds[completionId]) {
                setProcessedCompletionIds(prev => ({...prev, [completionId]: true}));
                
                const totalPages = matchingTask.total_pages || 0;
                if (totalPages > 0) {
                  statsRef.current = {
                    ...statsRef.current,
                    documents_count: statsRef.current.documents_count + 1
                  };
                  totalPageCountRef.current = totalPageCountRef.current + totalPages;
                  
                  setStats(prev => ({
                    ...prev,
                    documents_count: prev.documents_count + 1
                  }));
                  setTotalPageCount(prev => prev + totalPages);
                }
              }
              
              setTimeout(() => saveSessionState(), 1000);
              
              setTimeout(() => {
                fetchRagDocuments(true);
                setTimeout(() => {
                  resetProcessingState();
                  clearTaskFromLocalStorage();
                }, 1000);
              }, 3000);
              
              return;
            } else if (matchingTask.status === 'failed') {
              resetProcessingState();
              clearTaskFromLocalStorage();
              message.error(matchingTask.error ? `处理失败: ${matchingTask.error}` : '处理失败');
              setTimeout(() => fetchRagDocuments(true), 1000);
              return;
            }
            
            const backendProgress = matchingTask.progress !== undefined ? matchingTask.progress : 0;
            lastProgressValue = Math.max(lastProgressValue, backendProgress);
            setProcessingProgress(lastProgressValue);
            
            const newCurrentPage = matchingTask.current_page || 0;
            const newTotalPages = matchingTask.total_pages || processingInfo?.total_pages || 0;
            const newProcessedPages = matchingTask.processed_pages || 0;
            
            setProcessingInfo(prevInfo => ({
              task_id: docId,
              doc_id: matchingTask.doc_id,
              current_step: matchingTask.current_step,
              current_page: Math.max(newCurrentPage, prevInfo?.current_page || 0),
              total_pages: Math.max(newTotalPages, prevInfo?.total_pages || 0),
              processed_pages: Math.max(newProcessedPages, prevInfo?.processed_pages || 0)
            }));
            
            // 更新文档列表中的对应文档状态
            const updatedDocs = documents.map(doc => {
              if ((doc.doc_id === docId || doc.int_doc_id === docId || doc.filename === matchingTask.file_name)) {
                return {
                  ...doc,
                  status: 'processing',
                  is_processing: true,
                  total_pages: matchingTask.total_pages || doc.total_pages || 0,
                  images_count: matchingTask.total_pages || doc.images_count || 0,
                  processed_pages: matchingTask.current_page || 0,
                  current_page: matchingTask.current_page || 0
                };
              }
              return doc;
            });
            
            if (JSON.stringify(documents) !== JSON.stringify(updatedDocs)) {
              setDocuments(updatedDocs);
              setTimeout(() => saveSessionState(), 500);
            }
            
            statsUpdateCounter++;
            if (statsUpdateCounter >= 5) {
              statsUpdateCounter = 0;
              saveSessionState();
            }
            
            retryCount = 0;
            
            // 设置下次检查时间
            let checkInterval = 5000;
            
            if (matchingTask.total_pages > 200) {
              if (matchingTask.current_step === 'generating_embeddings') {
                checkInterval = 10000;
              } else if (matchingTask.current_step === 'inserting_data') {
                checkInterval = 12000;
              } else if (
                matchingTask.current_step === 'processing_pdf' ||
                matchingTask.current_step === 'extracting_text'
              ) {
                checkInterval = 8000;
              } else if (
                matchingTask.current_step === 'saving_pages' ||
                matchingTask.current_step === 'chunking_text'
              ) {
                if (matchingTask.total_pages > 500) {
                  checkInterval = 10000;
                } else {
                  checkInterval = 8000;
                }
              }
            } else if (matchingTask.total_pages > 50) {
              checkInterval = 6000;
            }
            
            if (backendProgress > 90) {
              checkInterval = Math.min(checkInterval, 4000);
            }
            
            const jitter = Math.floor(Math.random() * (checkInterval * 0.2));
            const finalInterval = checkInterval + jitter;
            
            setTimeout(checkProgressByPolling, finalInterval);
          } else {
            retryCount++;
            
            if (retryCount >= maxRetries) {
              resetProcessingState();
              clearTaskFromLocalStorage();
              message.error('无法获取处理进度,任务可能已完成或被取消');
              return;
            }
            
            const retryDelay = Math.min(2000 * Math.pow(1.2, retryCount), 10000);
            setTimeout(checkProgressByPolling, retryDelay);
          }
        } else {
          retryCount++;
          
          if (retryCount >= maxRetries) {
            resetProcessingState();
            clearTaskFromLocalStorage();
            message.error('无法获取处理进度,请检查网络连接');
            return;
          }
          
          const retryDelay = Math.min(3000 * Math.pow(1.5, retryCount), 15000);
          setTimeout(checkProgressByPolling, retryDelay);
        }
      } catch (error) {
        retryCount++;
        
        if (retryCount >= maxRetries) {
          resetProcessingState();
          clearTaskFromLocalStorage();
          message.error('检查进度失败,请重新上传或检查网络');
          return;
        }
        
        const retryDelay = Math.min(3000 * Math.pow(1.5, retryCount), 15000);
        setTimeout(checkProgressByPolling, retryDelay);
      }
    };
    
    checkProgressByPolling();
  };
  
  // 处理文件上传
  const handleUpload = async (options) => {
    const { file, onSuccess, onError, onProgress } = options;
    resetProcessingState();
    
    const supportedTypes = ['.pdf'];
    const fileExt = '.' + file.name.split('.').pop().toLowerCase();
    const isValidType = supportedTypes.includes(fileExt);
    if (!isValidType) {
      message.error('只支持PDF文件格式');
      onError('不支持的文件类型');
      return;
    }
      
    if (file.size > 50 * 1024 * 1024) {
      message.error('文件过大,请上传小于50MB的文件');
      onError('文件过大');
      return;
    }
    
    setUploading(true);
    setProcessing(true);
    setProcessingFile(file.name);
    setProcessingProgress(0);
    
    const formData = new FormData();
    formData.append('file', file);
    formData.append('config_id', currentConfig);
    formData.append('user_id', currentUserId);
    
    try {
      const uploadKey = 'uploadRagFile';
      message.loading({ content: `正在上传 ${file.name}...`, key: uploadKey });
      
      const xhr = new XMLHttpRequest();
      xhr.open('POST', `${ragBaseUrl}/upload_rag_document`, true);
      
      xhr.upload.onprogress = event => {
        if (event.lengthComputable) {
          const percent = Math.round((event.loaded / event.total) * 100);
          onProgress({ percent });
          
          const uploadProgress = Math.min(30, Math.round((percent / 100) * 30));
          setProcessingProgress(uploadProgress);
        }
      };
      
      xhr.onload = () => {
        if (xhr.status === 200) {
          const response = JSON.parse(xhr.responseText);
          
          message.success({
            content: `${file.name} 上传成功,正在处理中...`,
            key: uploadKey,
            duration: 3
          });
          onSuccess(response);
          
          const trackingId = response.tracking_id || response.doc_id || response.task_id;
          
          if (trackingId) {
            saveTaskToLocalStorage(trackingId, file.name, currentConfig);
            
            setProcessingInfo({
              task_id: trackingId,
              doc_id: response.doc_id,
              original_task_id: response.task_id,
              tracking_id: trackingId,
              total_pages: response.total_pages || 0
            });
            
            setProcessingProgress(30);
            
            setTimeout(() => {
              trackProcessingProgress(trackingId, file.name, false, 30);
            }, 500);
          } else {
            setProcessingProgress(30);
          }
        } else {
          let errorMsg = '上传失败';
          try {
            const response = JSON.parse(xhr.responseText);
            errorMsg = response.error || errorMsg;
          } catch (e) {}
          
          message.error({ content: errorMsg, key: uploadKey });
          onError(errorMsg);
          setProcessing(false);
        }
        setUploading(false);
      };
      
      xhr.onerror = () => {
        message.error({ content: '网络错误,上传失败', key: uploadKey });
        onError('网络错误');
        setUploading(false);
        setProcessing(false);
      };
      
      xhr.send(formData);
    } catch (error) {
      console.error('上传知识库文档错误:', error);
      message.error('上传知识库文档时发生错误');
      setUploading(false);
      setProcessing(false);
      onError(error);
    }
  };
  
  // 检查是否为只读配置
  const isReadonlyConfig = (configId) => {
    return configId === 'default';
  };

  // 检查当前用户是否可以操作该配置
  const canUserOperateConfig = (configId) => {
    if (configId === 'none') return false; // 无知识库不能操作
    if (configId === 'default') return false; // 默认库只读
    return true; // 用户私有库可以操作
  };
  
  // 删除文档
  const handleDelete = async (docId, fileName) => {
    Modal.confirm({
      title: '确认删除',
      content: `确定要从知识库中删除文档 "${fileName}" 吗?`,
      okText: '删除',
      okType: 'danger',
      cancelText: '取消',
      onOk: async () => {
        try {
          setDocuments(prev => prev.filter(doc => doc.doc_id !== docId));
          
          const deleteKey = 'deleteDocument';
          message.loading({ content: '正在删除文档...', key: deleteKey });
          
          const response = await fetch(`${ragBaseUrl}/delete_rag_document`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
               doc_id: docId,
              config_id: currentConfig,
              user_id: currentUserId,  // 添加用户ID
              physical_delete: true
            })
          });
          
          if (response.ok) {
            const data = await response.json();
            
            if (data.deleted || data.success) {
              let successMessage = `文档 "${fileName}" 已成功从知识库中删除`;
              
              message.success({ content: successMessage, key: deleteKey });
              
              if (data.filename) {
                setDocuments(prev => prev.filter(doc =>
                   doc.filename !== data.filename || doc.doc_id === docId
                ));
              }
              
              // 更新统计数据
             setStats(prev => ({
              ...prev,
              documents_count: Math.max(0, prev.documents_count - 1)
            }));
            
            const deletedPages = documents.find(doc => doc.doc_id === docId)?.total_pages || 
                               documents.find(doc => doc.doc_id === docId)?.images_count || 0;
            
            setTotalPageCount(prev => Math.max(0, prev - deletedPages));
            
            statsRef.current = {
              ...statsRef.current,
              documents_count: Math.max(0, statsRef.current.documents_count - 1)
            };
            totalPageCountRef.current = Math.max(0, totalPageCountRef.current - deletedPages);
            
            setTimeout(() => saveSessionState(), 500);
          } else {
            message.warning({
               content: '文档部分删除,将刷新列表',
               key: deleteKey
             });
            fetchRagDocuments(true);
          }
        } else {
          let errorMsg = '删除失败';
          try {
            const errorData = await response.json();
            errorMsg = errorData.error || '删除失败,请重试';
          } catch (e) {}
          
          message.error({ content: errorMsg, key: deleteKey });
          fetchRagDocuments(true);
        }
      } catch (error) {
        console.error('删除文档错误:', error);
        message.error('删除过程中发生错误');
        fetchRagDocuments(true);
      }
    }
  });
};

// 创建新的知识库配置
const createNewConfiguration = async () => {
  if (!newConfigName) {
    message.error('请输入知识库名称');
    return;
  }

  setCreatingConfig(true);
  const loadingKey = 'createConfig';
  message.loading({ content: '正在创建知识库...', key: loadingKey });
  
  try {
    const response = await fetch(`${ragBaseUrl}/create_rag_configuration`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: newConfigName,
        user_id: currentUserId  // 添加用户ID
      })
    });
  
    if (response.ok) {
      const data = await response.json();
      const config = data.config || data.configuration;
      message.success({ content: `创建知识库配置"${config?.display_name || config?.name || config?.id || newConfigName}"成功`, key: loadingKey });
      setNewConfigModalVisible(false);
      setNewConfigName('');
      
      // 延迟刷新确保后端同步完成
      setTimeout(async () => {
        await fetchConfigurations();
        // 强制重新渲染
        setConfigurations(prev => [...prev]);
      }, 1000);
    } else {
      const data = await response.json();
      message.error({ content: data.error || '创建知识库配置失败', key: loadingKey });
    }
  } catch (error) {
    console.error('创建知识库配置错误:', error);
    message.error({ content: '创建知识库配置时发生错误', key: loadingKey });
  } finally {
    setCreatingConfig(false);
  }
};

// 设置活跃知识库配置
const setActiveConfiguration = async (configId) => {
  setSwitchingConfig(true);
  const loadingKey = 'settingActive';
  message.loading({ content: '正在设置当前知识库...', key: loadingKey });
  
  try {
    const response = await fetch(`${ragBaseUrl}/set_active_configuration`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ 
        config_id: configId,
        user_id: currentUserId  // 添加用户ID
      }),
      signal: AbortSignal.timeout(30000)
    });
    
    if (response.ok) {
      const data = await response.json();
      const targetConfig = configurations.find(config => config.id === configId);
      const configName =
        data.config_name ||
        targetConfig?.display_name ||
        targetConfig?.name ||
        configId;
      if (data.warning) {
        message.warning({ content: data.warning, key: loadingKey, duration: 4 });
      } else {
        message.success({
          content: data.message || `当前知识库已切换为 ${configName}`,
          key: loadingKey,
        });
      }
      
      if (configId !== currentConfig) {
        setDocuments([]);
        setStats({
          documents_count: 0,
          total_chunks: 0
        });
        setTotalPageCount(0);
        setMilvusStatus({});

        documentsRef.current = [];
        statsRef.current = {
          documents_count: 0,
          total_chunks: 0
        };
        totalPageCountRef.current = 0;

        setLastConfigId('');
        setCurrentConfig(configId);
      }
      
      // 更新本地配置状态，避免重复请求
      setConfigurations(prev => prev.map(config => ({
        ...config,
        active: config.id === configId
      })));
      
      setTimeout(() => fetchRagDocuments(false), 500);
    } else {
      const data = await response.json();
      message.error({ content: data.error || '设置当前知识库失败', key: loadingKey });
    }
  } catch (error) {
    console.error('设置当前知识库错误:', error);
    
    if (error.name === 'AbortError') {
      message.error({ content: '设置当前知识库超时,请重试', key: loadingKey });
    } else {
      message.error({ content: '设置当前知识库时发生错误', key: loadingKey });
    }
  } finally {
    setSwitchingConfig(false);
  }
};

// 删除知识库配置
const handleDeleteConfig = async (configId) => {
  if (configId === 'default') {
    message.error('默认知识库配置不能删除');
    return;
  }
  
  // 先获取要删除的配置名称
  const configToDelete = configurations.find(config => config.id === configId);
  const configDisplayName = configToDelete ? (configToDelete.display_name || configToDelete.name) : configId;
  
  Modal.confirm({
    title: '确认删除知识库',
    content: `确定要删除知识库"${configDisplayName}"吗?所有相关文档将一同删除,此操作不可恢复。`,
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    onOk: async () => {
      setDeletingConfig(true);
      
      const deleteKey = 'deleteConfig';
      message.loading({ content: `正在删除知识库"${configDisplayName}"...`, key: deleteKey });
      
      try {
        const response = await fetch(`${ragBaseUrl}/delete_rag_configuration`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
             config_id: configId,
            user_id: currentUserId,  // 添加用户ID
            force_delete: true
          }),
          signal: AbortSignal.timeout(65000)  // 增加到65秒以适应后端60秒超时
        });
        
        if (response) {
          try {
            const data = await response.json();
            
            if (data.success || data.deleted) {
              // 显示成功消息
              if (data.timeout) {
                // 后端超时但操作继续进行的情况
                message.info({
                   content: data.message || '删除操作正在后台进行，请稍后刷新查看结果',
                   key: deleteKey,
                   duration: 8
                 });
                 
                // 延迟刷新配置
                setTimeout(async () => {
                  await fetchConfigurations();
                  if (currentConfig === configId) {
                    setCurrentConfig('default');
                  }
                }, 5000);
              } else if (data.partial_success) {
                message.warning({
                   content: data.db_delete_error || `知识库"${configDisplayName}"已部分删除`,
                   key: deleteKey,
                   duration: 4
                 });
              } else {
                // 正常删除完成
                message.success({
                   content: `知识库"${configDisplayName}"已${response.ok ? '成功' : '部分'}删除`,
                   key: deleteKey
                 });
                 
                // 立即刷新配置
                setTimeout(async () => {
                  await fetchConfigurations();
                  if (currentConfig === configId) {
                    setCurrentConfig('default');
                  }
                }, 1000);
              }
            } else {
              message.warning({
                 content: data.error || '删除过程出现问题,将刷新配置列表',
                 key: deleteKey
               });
               
              // 刷新配置列表
              setTimeout(async () => {
                await fetchConfigurations();
                if (currentConfig === configId) {
                  setCurrentConfig('default');
                }
              }, 2000);
            }
          } catch (jsonError) {
            message.warning({
               content: '解析响应失败,将刷新配置列表',
               key: deleteKey
             });
             
            setTimeout(async () => {
              await fetchConfigurations();
              if (currentConfig === configId) {
                setCurrentConfig('default');
              }
            }, 2000);
          }
        } else {
          message.warning({
             content: '服务器未返回响应,将刷新配置列表',
             key: deleteKey
           });
           
          setTimeout(async () => {
            await fetchConfigurations();
            if (currentConfig === configId) {
              setCurrentConfig('default');
            }
          }, 2000);
        }
      } catch (error) {
        console.error('删除知识库错误:', error);
        
        if (error.name === 'TimeoutError') {
          message.info({
            content: '删除操作超时，但可能在后台继续进行，请稍后刷新查看结果',
            key: deleteKey,
            duration: 8
          });
        } else {
          message.warning({
             content: `${error.message || '删除过程中出现错误'},将刷新配置列表`,
             key: deleteKey
           });
        }
        
        // 延迟刷新配置列表
        setTimeout(async () => {
          await fetchConfigurations();
          if (currentConfig === configId) {
            setCurrentConfig('default');
          }
        }, error.name === 'TimeoutError' ? 5000 : 2000);
      } finally {
        setDeletingConfig(false);
      }
    }
  });
};

// 查看文档详情
const viewDocumentDetails = (document) => {
  setCurrentDocument(document);
  setModalVisible(true);
};

// 获取文件图标
const getFileIcon = (fileName) => {
  // 只支持PDF文件，统一使用PDF图标
  return <FileTextTwoTone />;
};
// const getFileIcon = (fileName) => {
//   const name = fileName.toLowerCase();
//   if (name.endsWith('.pdf')) {
//     return <FileTextTwoTone />;
//   } else if (name.endsWith('.py')  || name.endsWith('.sh') || name.endsWith('.cmd') ) {
//     return <CodeOutlined style={{ color: '#52c41a' }} />;
//   } else if (name.endsWith('.md')) {
//     return <FileTextTwoTone twoToneColor="#722ed1" />;
//   } else {
//     return <FileTextOutlined />;
//   }
// };

// 手动刷新文档列表
const handleRefresh = () => {
  if (loading) return;
  fetchRagDocuments(true);
};

// 表格列定义
const columns = [
  {
    title: '文件名',
    dataIndex: 'filename',
    key: 'filename',
    ellipsis: true,
    render: (text, record) => (
      <Button 
        type="link" 
        onClick={() => viewDocumentDetails(record)}
        icon={getFileIcon(text)}
        disabled={switchingConfig || deletingConfig}
        style={{ textAlign: 'left', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}
      >
        {text}
        {(record.images_count > 0 || record.total_pages > 0) && (
          <Tag color="blue" style={{ marginLeft: 8 }}>
            {record.total_pages || record.images_count}页
          </Tag>
        )}
      </Button>
    )
  },
  {
    title: '大小',
    dataIndex: 'file_size_formatted',
    key: 'file_size',
    width: 120,
    responsive: ['md']
  },
  // {
  //   title: '修改时间',
  //   dataIndex: 'last_modified_time',
  //   key: 'last_modified',
  //   width: 160,
  //   responsive: ['lg']
  // },
  {
    title: '状态',
    dataIndex: 'status',
    key: 'status',
    width: 120,
    render: (status, record) => {
      if (
        (record.processed === true) || 
        (record.status === 'processed') ||
        (record.processed_pages && record.processed_pages > 0) ||
        (record.chunks_count && record.chunks_count > 0) ||
        (record.images_count && record.images_count > 0)
      ) {
        return (<Tag color="green">已处理</Tag>);
      }
      
      switch(status) {
        case 'processed':
          return <Tag color="green">已处理</Tag>;
        case 'processing':
          return <Tag color="blue">处理中</Tag>;
        case 'deleted':
          return <Tag color="red">已删除</Tag>;
        case 'missing':
          return <Tag color="orange">文件缺失</Tag>;
        default:
          if (!record.file_exists) {
            return <Tag color="orange">文件缺失</Tag>;
          } else {
            return <Tag color="gray">待处理</Tag>;
          }
      }
    }
  },
  {
    title: '操作',
    key: 'action',
    width: 100,
    render: (_, record) => (
      <Button
        type="link"
        danger
        icon={<DeleteOutlined />}
        onClick={() => handleDelete(record.doc_id, record.filename)}
        disabled={processing || uploading || loading || switchingConfig || deletingConfig || !canUserOperateConfig(currentConfig)}
        title={!canUserOperateConfig(currentConfig) ? (currentConfig === 'default' ? '默认库为只读，不能删除' : '无知识库模式') : '删除文档'}
      />
    )
  }
];

return (
  <div className="rag-manager-container">
    <Card title={
      <div className="card-title">
        知识库管理
      </div>
    } className="rag-manager-card">
      {/* 知识库配置选择器 */}
      <div className="config-selector-container">
        <div className="config-selector">
          <span className="config-label">当前知识库：</span>
          <Select
            showSearch
            value={currentConfig}
            onChange={setCurrentConfig}
            style={{ minWidth: 200 }}
            disabled={switchingConfig || processing || uploading || deletingConfig || creatingConfig}
            optionFilterProp="children"
            filterOption={(input, option) =>
              (option?.children ?? '').toLowerCase().includes(input.toLowerCase())
            }
          >
            {configurations.map(config => (
              <Option key={config.id} value={config.id}>
                {config.display_name || config.name || config.id.replace(/^rag_/, '').replace(/^config_\d+_[a-f0-9]+$/, '自动检测知识库')}
                {config.active && " (当前活跃)"}
              </Option>
            ))}
          </Select>
          <Button 
            type="primary"
            onClick={() => setActiveConfiguration(currentConfig)}
            style={{ marginLeft: 8 }}
            loading={switchingConfig}
            disabled={
              switchingConfig || 
              processing || 
              uploading || 
              deletingConfig || 
              creatingConfig || 
              configurations.find(config => config.id === currentConfig && config.active)
            }
          >
            {switchingConfig ? '设置中...' : '设为当前知识库'}
          </Button>
          <Button 
            icon={<PlusOutlined />}
            onClick={() => setNewConfigModalVisible(true)}
            style={{ marginLeft: 8 }}
            disabled={switchingConfig || processing || uploading || deletingConfig || creatingConfig}
          >
            新建知识库
          </Button>
          {currentConfig !== 'default' && currentConfig !== 'none' &&(
            <Button 
              danger
              icon={<DeleteOutlined />}
              onClick={() => handleDeleteConfig(currentConfig)}
              style={{ marginLeft: 8 }}
              loading={deletingConfig}
              disabled={switchingConfig || processing || uploading || deletingConfig || creatingConfig}
            >
              {deletingConfig ? '删除中...' : '删除此知识库'}
            </Button>
          )}
        </div>
      </div>
      
      {/* 处理进度条 */}
      {processing && (
        <div className="processing-container">
          <div className="processing-info">
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '5px' }}>
              <span><strong>正在处理:</strong> {processingFile}</span>
            </div>
            
            <Progress 
              percent={Math.round(processingProgress)} 
              status="active" 
              format={percent => `${percent}%`}
              style={{ marginBottom: '5px' }}
            />
            
            {processingInfo && (
              <div className="processing-details" style={{ display: 'flex', justifyContent: 'space-between' }}>
                <Text>
                  <strong>当前阶段:</strong> {' '}
                  {processingInfo.current_step === 'extracting_text' ? '提取文本' :
                  processingInfo.current_step === 'chunking_text' ? '切分文本' :
                  processingInfo.current_step === 'converting_pdf' ? '转换PDF' :
                  processingInfo.current_step === 'saving_pages' ? '保存页面' :
                  processingInfo.current_step === 'generating_embeddings' ? '生成向量' :
                  processingInfo.current_step === 'inserting_data' ? '存储数据' :
                  processingInfo.current_step === 'finalizing' ? '完成处理' : '处理中'}
                </Text>
              </div>
            )}
          </div>
        </div>
      )}
      
      {/* 统计信息和操作按钮 */}
      <div className="rag-manager-header">
        <div className="rag-manager-stats">
          <Space size="large">
            <Statistic
              title="知识库文档数"
              value={currentConfig === 'none' ? '—' : (stats.documents_count || 0)}
              prefix={<FileTextOutlined />}
            />
            <Statistic 
              title="总页数"
              value={currentConfig === 'none' ? '—' : (totalPageCount || 0)}
              prefix={<FileImageOutlined />}
            />
          </Space>
        </div>
        
        <div className="rag-manager-actions">
          <Space>
            <Upload
              customRequest={handleUpload}
              showUploadList={false}
              disabled={processing || switchingConfig || deletingConfig || creatingConfig || !canUserOperateConfig(currentConfig)}
              accept=".pdf"
            >
              <Button 
                type="primary" 
                loading={uploading}
                icon={<UploadOutlined />}
                disabled={uploading || processing || switchingConfig || deletingConfig || creatingConfig || !canUserOperateConfig(currentConfig)}
              >
                {isReadonlyConfig(currentConfig) ? '默认库只读' : currentConfig === 'none' ? '无知识库' : '上传文档'}
              </Button>
            </Upload>
            
            <Button 
              onClick={handleRefresh}
              loading={loading}
              icon={<ReloadOutlined />}
              disabled={processing || switchingConfig || deletingConfig || creatingConfig}
            >
              刷新
            </Button>
          </Space>
        </div>
      </div>
                
      {/* 文档列表 */}
      <div className="rag-manager-table-shell">
        <Table
          columns={columns}
          dataSource={documents}
          rowKey="doc_id"
          loading={loading || switchingConfig}
          pagination={{ 
            pageSize: 10,
            showTotal: (total) => `共 ${total} 个文档`
          }}
          size="middle"
          scroll={{ y: 'calc(100vh - 400px)' }}
          locale={{
            emptyText: currentConfig === 'none' ? 
              <div style={{ textAlign: 'center', padding: '40px' }}>
                <div style={{ marginTop: '16px', color: '#999' }}>当前已选择"无"知识库</div>
                <div style={{ marginTop: '8px', color: '#ccc', fontSize: '14px' }}>
                  所有查询将不使用知识库进行检索
                </div>
              </div>
              :
              <div style={{ textAlign: 'center', padding: '40px' }}>
                <FileTextTwoTone style={{ fontSize: '48px', color: '#ccc' }} />
                <div style={{ marginTop: '16px', color: '#999' }}>当前知识库中没有文档</div>
                <div style={{ marginTop: '8px', color: '#ccc', fontSize: '14px' }}>
                  请点击"上传文档"按钮添加文档到知识库
                </div>
              </div>
          }}
        />
      </div>
    </Card>
    
    {/* 文档详情模态框 */}
    <Modal
      title={
        <span>
          <FileTextTwoTone /> 文档详情
        </span>
      }
      open={modalVisible}
      onCancel={() => setModalVisible(false)}
      footer={[
        <Button key="close" onClick={() => setModalVisible(false)}>
          关闭
        </Button>
      ]}
      width={600}
    >
      {currentDocument && (
        <div>
          <Paragraph>
            <Text strong>文件名:</Text> {currentDocument.filename}
          </Paragraph>
          <Paragraph>
            <Text strong>文件大小:</Text> {currentDocument.file_size_formatted}
          </Paragraph>
          <Paragraph>
            <Text strong>总页数:</Text> {currentDocument.total_pages || currentDocument.images_count || '未知'}
          </Paragraph>
          
          <Paragraph>
            <Text strong>处理状态:</Text> {
              (() => {
                if (
                  (currentDocument.processed === true) || 
                  (currentDocument.status === 'processed') ||
                  (currentDocument.processed_pages && currentDocument.processed_pages > 0) ||
                  (currentDocument.chunks_count && currentDocument.chunks_count > 0) ||
                  (currentDocument.images_count && currentDocument.images_count > 0)
                ) {
                  return <Tag color="success">已处理</Tag>;
                }
                
                const status = currentDocument.status;
                switch(status) {
                  case 'processed': return <Tag color="success">已处理</Tag>;
                  case 'processing': return <Tag color="warning">处理中</Tag>;
                  case 'missing': return <Tag color="error">文件缺失</Tag>;
                  case 'unprocessed': return <Tag color="warning">待处理</Tag>;
                  case 'untracked': return <Tag color="default">未添加</Tag>;
                  default: 
                    if (!currentDocument.file_exists) {
                      return <Tag color="error">文件缺失</Tag>;
                    } else {
                      return <Tag color="warning">待处理</Tag>;
                    }
                }
              })()
            }
          </Paragraph>
                      
          {currentDocument.processed && (
            <Paragraph>
              <Text strong>已处理块数:</Text> {currentDocument.chunks_count || currentDocument.images_count || 0}
            </Paragraph>
          )}
          
          <Paragraph>
            <Text strong>最后修改时间:</Text> {currentDocument.last_modified_time}
          </Paragraph>
          
          {/* <Paragraph>
            <Text strong>文档ID:</Text> {currentDocument.doc_id}
            {currentDocument.int_doc_id && (
              <Tooltip title="在Milvus中使用的整数ID">
                <Tag style={{ marginLeft: 8 }}>{currentDocument.int_doc_id}</Tag>
              </Tooltip>
            )}
          </Paragraph> */}
          
          {/* <Paragraph>
            <Text strong>知识库配置:</Text> {currentDocument.config_id || currentConfig}
          </Paragraph> */}
          
          {/* {(currentDocument.text_preview) && (
            <Paragraph>
              <Text strong>提取文本预览:</Text> 
              <div style={{ 
                padding: '8px', 
                background: '#f5f5f5', 
                borderRadius: '4px',
                marginTop: '8px',
                maxHeight: '150px',
                overflow: 'auto'
              }}>
                {currentDocument.text_preview}
              </div>
            </Paragraph>
          )} */}
          
          {/* {milvusStatus && milvusStatus.config_id === currentConfig && (
            <Paragraph>
              <Text strong>数据库信息:</Text>
              <div style={{ marginTop: '8px' }}>
                <Space direction="vertical">
                  <div>
                    {milvusStatus.database_name && (
                      <Tag color="purple">数据库: {milvusStatus.database_name}</Tag>
                    )}
                    <Tag color="cyan">集合: {milvusStatus.collection_name}</Tag>
                    <Tag color="blue">配置ID: {milvusStatus.config_id}</Tag>
                  </div>
                </Space>
              </div>
            </Paragraph>
          )} */}
        </div>
      )}
    </Modal>
    
    {/* 新建配置模态框 */}
    <Modal
      title="新建知识库配置"
      open={newConfigModalVisible}
      onCancel={() => !creatingConfig && setNewConfigModalVisible(false)}
      onOk={createNewConfiguration}
      okText={creatingConfig ? "创建中..." : "创建"}
      cancelText="取消"
      confirmLoading={creatingConfig}
      okButtonProps={{ disabled: creatingConfig }}
      cancelButtonProps={{ disabled: creatingConfig }}
    >
      <div style={{ marginBottom: 16 }}>
        <div style={{ marginBottom: 8 }}>
          <Text strong>知识库名称:</Text>
        </div>
        <Input 
          placeholder="请输入知识库名称" 
          value={newConfigName}
          onChange={e => setNewConfigName(e.target.value)}
          disabled={creatingConfig}
        />
      </div>
      <div>
      </div>
    </Modal>
  </div>
);
};

export default RagManager;
