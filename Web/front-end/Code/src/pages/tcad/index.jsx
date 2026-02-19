import './index.css';
import React, { useEffect, useMemo, useCallback, useState, useRef } from 'react';
import { Button, Form, Input, message, Upload, Modal } from 'antd';
import Cookies from 'js-cookie';
import { uploadTCADFile, fetchTCADStreaming, deleteTCADUploadedFile } from '../../api/tcadApi';
import { MESSAGE_TYPE } from '../../constants';
import ChatMessage from '../../components/chatMessage';
import { LoadingOutlined, CloudUploadOutlined, DatabaseOutlined, StopOutlined } from '@ant-design/icons';
import { Dropdown } from 'antd';
import { useDispatch } from 'react-redux';
import { updateMainPage } from '../../store/pageStore';
// 导入默认会话相关函数
import { DEFAULT_SESSION, createRealSessionAfterChat, isDefaultSession } from '../../components/history/history';

const Chatbot = ({ port }) => {
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(false);
  const [messages, setMessages] = useState([]);
  const [uploading, setUploading] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const [switchingRag, setSwitchingRag] = useState(false);
  const username = Cookies.get('user');
  const userId = Cookies.get('userid') || Cookies.get('userId'); // 添加回退，支持两种Cookie键
  const [fileList, setFileList] = useState([]);
  const [sessionId, setSessionId] = useState(Cookies.get('3')); // TCAD modelId = 3
  const [ragConfigurations, setRagConfigurations] = useState([]);
  const [currentRagConfig, setCurrentRagConfig] = useState(null);
  const dispatch = useDispatch();
  
  // API请求常量和函数
  const API_BASE_URL = `http://10.98.64.22:8080`;
  
  const apiRequest = async (endpoint, options = {}) => {
    const url = `${API_BASE_URL}/${endpoint}`;
    const defaultOptions = {
      headers: { "Content-Type": "application/json" },
      ...options
    };
    
    try {
      const response = await fetch(url, defaultOptions);
      
      if (!response.ok) {
        const errorText = await response.text();
        throw new Error(`HTTP error! status: ${response.status}, body: ${errorText}`);
      }
      
      const text = await response.text();
      
      if (!text || text.trim() === '') {
        if (endpoint === 'session/update') {
          return { success: true, message: 'Session updated successfully' };
        }
        return null;
      }
      
      try {
        return JSON.parse(text);
      } catch (jsonError) {
        if (endpoint === 'session/update') {
          return { success: true, message: text };
        }
        return text;
      }
    } catch (error) {
      throw error;
    }
  };

  // TCAD会话标题更新函数
  const updateSessionHeader = async (sessionId, userId, userMessage = '', botMessage = '') => {
    try {
      // 检查当前会话是否已经有自定义标题
      const currentSession = await apiRequest(`session/get?sessionId=${sessionId}`);
      if (currentSession && currentSession.header && currentSession.header !== '新会话') {
        return;
      }
      
      const messagesList = await apiRequest(`message/list-by-session?sessionId=${sessionId}`);
      
      if (!messagesList || !Array.isArray(messagesList)) {
        return;
      }
      
      const firstUserMessage = messagesList.find(m => m.userType === MESSAGE_TYPE.USER);
      
      if (firstUserMessage) {
        const userContent = userMessage || firstUserMessage.content;
        
        let generatedTitle = '';
        
        // 使用简单的标题生成策略
        if (userContent.includes('文件上传') || userContent.includes('.sde') || userContent.includes('.cmd')) {
          generatedTitle = 'TCAD文件上传';
        } else if (userContent.includes('仿真') || userContent.includes('simulation') || userContent.includes('TCAD')) {
          generatedTitle = 'TCAD仿真';
        } else {
          // 使用用户输入的前8个字符，过滤HTML标签
          const cleanContent = userContent.replace(/<[^>]*>/g, '');
          generatedTitle = cleanContent.slice(0, 8) || '新对话';
        }

        const headerUpdate = {
          createTime: new Date().toISOString(),
          header: generatedTitle,
          lastActive: new Date().toISOString(),
          modelId: 3, // TCAD modelId = 3
          sessionId: sessionId,
          status: 1,
          userId: parseInt(userId),
        };

        await apiRequest("session/update", {
          method: "POST",
          body: JSON.stringify(headerUpdate)
        });
        
        // 触发历史更新事件
        window.sessionUpdated = Date.now();
        window.dispatchEvent(new Event("sessionUpdated"));
        
        setTimeout(() => {
          window.dispatchEvent(new Event("sessionUpdated"));
        }, 500);
      }
    } catch (error) {
      // 不抛出错误，让主流程继续
    }
  };
  
  
  // 使用TCAD负载均衡器，不再需要随机端口
  // const getRandomTcadPort = () => {
  //   const tcadPorts = [5004, 5014, 5025, 5028]; // 本地TCAD服务端口(4个实例)
  //   const randomPort = tcadPorts[Math.floor(Math.random() * tcadPorts.length)];
  //   // console.log(`随机选择TCAD端口: ${randomPort}`);
  //   return randomPort;
  // };
  
  // 新增：用于存储流式请求引用
  const streamRequestRef = useRef(null);
  
  const FILE_DELETE_TIMEOUT = 10 * 1000;
  const fileUploadTimesRef = useRef({});
  const currentRagConfigRef = useRef(null);

  const [deletedFiles, setDeletedFiles] = useState(() => {
    try {
      const saved = localStorage.getItem(`deletedFiles_${sessionId}`);
      return saved ? JSON.parse(saved) : {};
    } catch (e) {
      return {};
    }
  });

  useEffect(() => {
    currentRagConfigRef.current = currentRagConfig;
  }, [currentRagConfig]);

  const fetchRagConfigurations = useCallback(async () => {
    try {
      const savedConfig = localStorage.getItem(`ragConfig_${sessionId}`);
      const response = await fetch(`http://10.98.64.22:5100/get_rag_configurations?user_id=${userId || 'default'}`);
      
      if (response.ok) {
        const data = await response.json();
        setRagConfigurations(data.configurations);
        
        if (savedConfig === 'none') {
          setCurrentRagConfig('none');
          currentRagConfigRef.current = 'none';
          return;
        }
        
        if (savedConfig && data.configurations.some(c => c.id === savedConfig)) {
          setCurrentRagConfig(savedConfig);
          currentRagConfigRef.current = savedConfig;
          return;
        }
        
        const activeConfig = data.configurations.find(c => c.active);
        if (activeConfig) {
          setCurrentRagConfig(activeConfig.id);
          currentRagConfigRef.current = activeConfig.id;
        }
      } else {
        message.error('获取知识库配置失败');
      }
    } catch (error) {
      message.error('获取知识库配置失败，请确保RAG Manager服务可用');
    }
  }, [sessionId]);

  useEffect(() => {
    fetchRagConfigurations();
  }, [fetchRagConfigurations]);

  useEffect(() => {
    const handleSessionChange = () => {
      const newSessionId = Cookies.get('3');
      if (newSessionId !== sessionId) {
        setSessionId(newSessionId);
        fileUploadTimesRef.current = {};
        
        try {
          const saved = localStorage.getItem(`deletedFiles_${newSessionId}`);
          setDeletedFiles(saved ? JSON.parse(saved) : {});
        } catch (e) {
          setDeletedFiles({});
        }
      }
    };

    // 🔧 监听会话删除事件
    const handleSessionDeleted = (event) => {
      const { deletedSessionId, modelId, shouldResetToDefault } = event.detail;
      
      if (modelId === 3 && shouldResetToDefault) {
        // 🔧 强制重置所有状态
        setMessages([]);
        setLoading(false);
        setStreaming(false);
        setFileList([]);
        
        // 清除文件相关状态
        fileUploadTimesRef.current = {};
        setDeletedFiles({});
        
        // 🔧 强制设置为DEFAULT_SESSION并立即触发状态更新
        setSessionId(DEFAULT_SESSION);
        
        // 🔧 延迟检查并更新sessionId，确保Cookie已经更新
        setTimeout(() => {
          const newSessionId = Cookies.get('3');
          if (newSessionId !== sessionId) {
            setSessionId(newSessionId);
          }
        }, 100);
      }
    };

    const interval = setInterval(handleSessionChange, 1000);
    window.addEventListener('sessionDeleted', handleSessionDeleted);
    
    return () => {
      clearInterval(interval);
      window.removeEventListener('sessionDeleted', handleSessionDeleted);
    };
  }, [sessionId]);

  useEffect(() => {
    if (sessionId && sessionId !== DEFAULT_SESSION) {
      fetch(`http://10.98.64.22:8080/message/list-by-session?sessionId=${sessionId}`)
        .then(response => response.json())
        .then(data => {
          const filteredData = data.filter(msg => {
            if (msg.type === 'file' || (msg.fileInfo && typeof msg.fileInfo === 'string')) {
              let fileInfo;
              try {
                fileInfo = typeof msg.fileInfo === 'string' ? JSON.parse(msg.fileInfo) : msg.fileInfo;
              } catch (e) {
                fileInfo = msg.fileInfo;
              }
              
              const fileName = fileInfo?.fileName || fileInfo?.name || msg.fileName;
              
              if (fileName && deletedFiles[fileName]) {
                return false;
              }
            }
            
            if (msg.content) {
              for (const fileName in deletedFiles) {
                if (msg.content.includes(`${fileName} 文件上传`) || msg.content.includes(`${fileName}文件上传`) ||
                    msg.content.includes(`${fileName} 文件上传成功`) || msg.content.includes(`${fileName}文件上传成功`)) {
                  return false;
                }
              }
            }
            
            return true;
          });
          
          setMessages(filteredData);
        })
        .catch(error => {
          setMessages([]);
        });
    } else if (sessionId === DEFAULT_SESSION) {
      setMessages([]);
    } else {
      setMessages([]);
    }
  }, [sessionId, deletedFiles]);

  const handleFileDelete = async (messageId, fileInfo) => {
    if (!fileInfo || (!fileInfo.fileName && !fileInfo.name)) {
      message.error('文件信息不完整，无法撤回');
      return;
    }
    
    const fileName = fileInfo.fileName || fileInfo.name;
    const uploadTime = fileUploadTimesRef.current[fileName];
    
    const currentTime = Date.now();
    const timeElapsed = uploadTime ? (currentTime - uploadTime) : FILE_DELETE_TIMEOUT + 1;
    
    if (timeElapsed > FILE_DELETE_TIMEOUT) {
      message.warning(`文件上传超过${FILE_DELETE_TIMEOUT/1000}秒，无法撤回`);
      return;
    }
    
    return new Promise((resolve) => {
      Modal.confirm({
        title: '确认撤回文件',
        content: `确定要撤回文件 "${fileName}" 吗？`,
        okText: '确认撤回',
        cancelText: '取消',
        onOk: async () => {
          try {
            const data = {
              conversation_id: sessionId,
              file_name: fileName
            };
            
            const loadingKey = 'deleteFile';
            message.loading({ content: '正在撤回文件...', key: loadingKey });
            
            const response = await deleteTCADUploadedFile(data);
            
            if (response?.status === 200 && response?.data?.isDeleted) {
              delete fileUploadTimesRef.current[fileName];
              
              try {
                await fetch(`${process.env.REACT_APP_TCAD_LOAD_BALANCER_URL || 'http://10.98.64.22:5102'}/clear_file_context`, {
                  method: 'POST',
                  headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({
                    conversation_id: sessionId,
                    file_name: fileName
                  })
                });
              } catch (err) {
              }
              
              setMessages(prevMessages => 
                prevMessages.filter(msg => {
                  const isTargetMessage = msg.messageId === messageId || 
                                        (msg.fileInfo && 
                                        (msg.fileInfo.fileName === fileName || 
                                          msg.fileInfo.name === fileName)) ||
                                        msg.fileName === fileName;
                  
                  const isBotSuccessMessage = msg.userType === MESSAGE_TYPE.BOT && 
                                            msg.content && 
                                            msg.content.includes(`${fileName} 文件上传成功`);
                  
                  return !(isTargetMessage || isBotSuccessMessage);
                })
              );
              
              setDeletedFiles(prev => {
                const updated = { ...prev, [fileName]: true };
                localStorage.setItem(`deletedFiles_${sessionId}`, JSON.stringify(updated));
                return updated;
              });
              
              message.success({ content: '文件及相关消息已成功删除', key: loadingKey, duration: 2 });
              
              window.sessionUpdated = Date.now();
              window.dispatchEvent(new Event("sessionUpdated"));
              
              resolve(true);
            } else {
              message.error({ content: '撤回文件失败', key: loadingKey, duration: 2 });
              resolve(false);
            }
          } catch (error) {
            message.error('撤回文件时发生错误');
            resolve(false);
          }
        },
        onCancel: () => {
          message.info('已取消撤回文件');
          resolve(false);
        }
      });
    });
  };

  const onhandleFinished = async () => {
    const values = await form.getFieldsValue();
    
    if (!values?.content) return;
    
    setLoading(true);
    setStreaming(true);
    
    let actualSessionId = sessionId;
    let isInDefaultSession = sessionId === DEFAULT_SESSION;
    
    // 如果是默认会话状态，先创建真实会话
    if (isInDefaultSession) {
      const newSessionId = await createRealSessionAfterChat(3); // modelId = 3 for TCAD
      if (newSessionId) {
        actualSessionId = newSessionId;
        setSessionId(newSessionId); // 更新当前组件的sessionId
      } else {
        setLoading(false);
        setStreaming(false);
        message.error('创建会话失败');
        return;
      }
    }
    
    const messageListResponse = await fetch("http://10.98.64.22:8080/message/list-all");
    const allMessages = await messageListResponse.json();
    const maxMessageId = allMessages.length ? Math.max(...allMessages.map(msg => msg.messageId)) : 0;
    
    const newMessage = {
      content: values.content,
      messageId: maxMessageId + 1,
      modelId: 3,
      sessionId: actualSessionId,
      timestamp: new Date().toISOString(),
      userId: userId ? parseInt(userId) : 1, // 确保userId是数字类型
      userType: MESSAGE_TYPE.USER,
    };
    
    const saveResult = await fetch("http://10.98.64.22:8080/message/add", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(newMessage),
    });
    
    // 🔧 检查用户消息是否保存成功
    if (!saveResult.ok) {
      setLoading(false);
      setStreaming(false);
      message.error('保存消息失败，请重试');
      return;
    }
    
    const tempBotMessage = {
      content: "正在思考中...", 
      messageId: maxMessageId + 2,
      modelId: 3,
      sessionId: actualSessionId,
      timestamp: new Date().toISOString(),
      userId: userId ? parseInt(userId) : 1, // 确保userId是数字类型
      userType: MESSAGE_TYPE.BOT,
      streaming: true,
      isLoading: true
    };
    
    setMessages(prevMessages => [...prevMessages, newMessage, tempBotMessage]);
    await form.resetFields();
    
    const params = { 
      user_id: username, 
      message: values.content,
      conversation_id: actualSessionId,
      config_id: currentRagConfigRef.current === 'none' ? 'none' : (currentRagConfigRef.current || 'default')
    };
    
    try {
      let fullResponse = '';
      let processingMode = null;
      
      // 将fetchTCADStreaming的返回值存储到ref中
      streamRequestRef.current = fetchTCADStreaming(
        params, 
        (data) => {
          if (data) {
            // 检查是否有中止标志
            if (data.aborted) {
              setMessages(prevMessages => {
                const updatedMessages = [...prevMessages];
                const lastIndex = updatedMessages.length - 1;
                
                if (lastIndex >= 0 && updatedMessages[lastIndex].streaming) {
                  updatedMessages[lastIndex] = {
                    ...updatedMessages[lastIndex],
                    content: updatedMessages[lastIndex].content + "\n\n[已中止回答]",
                    streaming: false,
                    isAborted: true
                  };
                }
                return updatedMessages;
              });
              
              setStreaming(false);
              setLoading(false);
              message.success({ content: '已中止当前回答', key: 'abortMessage' });
              return;
            }
            
            if (data.mode_info) {
              processingMode = data.processing_mode;
              
              let loadingMessage = '';
              switch (processingMode) {
                case 'simulation':
                  loadingMessage = '正在准备TCAD仿真环境...';
                  break;
                case 'generate':
                  if (data.config_id === 'none') {
                    loadingMessage = '正在准备生成TCAD代码...';
                  } else {
                    const configName = ragConfigurations.find(c => c.id === data.config_id)?.name || '知识库';
                    loadingMessage = `正在基于${configName}生成TCAD代码...`;
                  }
                  break;
                case 'qna':
                  if (data.config_id === 'none') {
                    loadingMessage = '正在生成回答...';
                  } else {
                    const configName = ragConfigurations.find(c => c.id === data.config_id)?.name || '知识库';
                    loadingMessage = `正在检索${configName}...`;
                  }
                  break;
                default:
                  loadingMessage = '正在处理您的请求...';
              }
              
              setMessages(prevMessages => {
                const updatedMessages = [...prevMessages];
                const lastIndex = updatedMessages.length - 1;
                
                if (lastIndex >= 0 && updatedMessages[lastIndex].isLoading) {
                  updatedMessages[lastIndex] = {
                    ...updatedMessages[lastIndex],
                    content: loadingMessage
                  };
                }
                
                return updatedMessages;
              });
              
              return;
            }
            
            if (data.start_streaming) {
              setMessages(prevMessages => {
                const updatedMessages = [...prevMessages];
                const lastIndex = updatedMessages.length - 1;
                
                if (lastIndex >= 0 && updatedMessages[lastIndex].streaming && updatedMessages[lastIndex].isLoading) {
                  updatedMessages[lastIndex] = {
                    ...updatedMessages[lastIndex],
                    request_id: data.request_id,
                    content: "",
                    isLoading: false
                  };
                }
                
                return updatedMessages;
              });
              return;
            }
            
            if (data.chunk) {
              fullResponse += data.chunk;
              
              setMessages(prevMessages => {
                const updatedMessages = [...prevMessages];
                const lastIndex = updatedMessages.length - 1;
                
                if (lastIndex >= 0 && updatedMessages[lastIndex].streaming) {
                  if (updatedMessages[lastIndex].isLoading) {
                    updatedMessages[lastIndex] = {
                      ...updatedMessages[lastIndex],
                      content: data.chunk,
                      isLoading: false
                    };
                  } else {
                    updatedMessages[lastIndex] = {
                      ...updatedMessages[lastIndex],
                      content: fullResponse
                    };
                  }
                }
                
                return updatedMessages;
              });
            }
          }
        },
        async () => {
          setStreaming(false);
          setLoading(false);
          
          const finalBotMessage = {
            content: fullResponse,
            messageId: maxMessageId + 2,
            modelId: 3,
            sessionId: actualSessionId,
            timestamp: new Date().toISOString(),
            userId: userId ? parseInt(userId) : 1, // 确保userId是数字类型
            userType: MESSAGE_TYPE.BOT,
          };
          
          const botSaveResult = await fetch("http://10.98.64.22:8080/message/add", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(finalBotMessage),
          });
          
          // 🔧 检查机器人消息是否保存成功
          if (!botSaveResult.ok) {
            message.error('保存AI回复失败');
            return;
          }
          
          setMessages(prevMessages => {
            const updatedMessages = [...prevMessages];
            const lastIndex = updatedMessages.length - 1;
            
            if (lastIndex >= 0 && updatedMessages[lastIndex].streaming) {
              updatedMessages[lastIndex] = {
                ...updatedMessages[lastIndex],
                streaming: false,
              };
            }
            
            return updatedMessages;
          });
          
          try {
            // 🔧 简单触发历史更新
            window.sessionUpdated = Date.now();
            window.dispatchEvent(new Event("sessionUpdated"));
            
            // 添加标题更新
            await updateSessionHeader(actualSessionId, userId, values.content, fullResponse);
          } catch (error) {
            console.error('TCAD触发历史更新失败:', error);
          }
        },
        (error) => {
          setStreaming(false);
          setLoading(false);
          
          let errorMessage = "获取回答失败，请稍后重试";
          if (error && error.message && error.message.includes("prematurely")) {
            errorMessage = "网络连接中断，请检查网络后重试";
          }
          message.error(errorMessage);
          
          setMessages(prevMessages => {
            const updatedMessages = [...prevMessages];
            const lastIndex = updatedMessages.length - 1;
            
            if (lastIndex >= 0 && updatedMessages[lastIndex].streaming) {
              updatedMessages[lastIndex] = {
                ...updatedMessages[lastIndex],
                content: `发生错误: ${errorMessage}`,
                streaming: false,
                isLoading: false,
                isError: true
              };
              return updatedMessages;
            }
            
            return prevMessages.filter(msg => !msg.streaming);
          });
        }
      );
    } catch (e) {
      setMessages(prevMessages => prevMessages.filter(msg => !msg.streaming));
      setLoading(false);
      setStreaming(false);
      return '请求出错啦';
    }
  };

  const beforeUpload = (file) => {
    setFileList([]);
    if (file.size / 1024 / 1024 > 15) {
      message.error("文件大小限制在15MB以内");
      return Upload.LIST_IGNORE;
    }
    return true;
  };

  const onUploadFile = useCallback(
    async (file, onSuccess, onError) => {
      try {
        // 🔧 强制重新读取最新的会话ID，避免使用过期缓存
        let currentSessionId = Cookies.get('3');

        // 如果状态和cookie不一致，更新状态
        if (currentSessionId && currentSessionId !== sessionId) {
          setSessionId(currentSessionId);
        }

        // 🔧 关键修复：如果是DEFAULT_SESSION或无效会话，创建真实会话
        if (!currentSessionId || currentSessionId === DEFAULT_SESSION) {
          const newSessionId = await createRealSessionAfterChat(3); // TCAD modelId = 3
          if (newSessionId) {
            currentSessionId = newSessionId;
            setSessionId(currentSessionId);
            Cookies.set(3, currentSessionId, { expires: 7 }); // 使用正确的cookie键
          } else {
            throw new Error("创建会话失败");
          }
        }

        var formData = new FormData();
        formData.append('file', file);
        formData.append('type', file.type);
        formData.append('conversation_id', currentSessionId);
        formData.append('user_id', username); // 添加用户ID

        const configId = currentRagConfig || 'default'; 
        formData.append('config_id', configId);

        const messageListResponse = await fetch("http://10.98.64.22:8080/message/list-all");
        const allMessages = await messageListResponse.json();
        const maxMessageId = allMessages.length ? Math.max(...allMessages.map(msg => msg.messageId)) : 0;

        const uploadTime = Date.now();
        fileUploadTimesRef.current[file.name] = uploadTime;

        const newMessage = {
          content: `${file.name} 文件上传`,
          messageId: maxMessageId + 1,
          modelId: 3,
          sessionId: currentSessionId,
          timestamp: new Date().toISOString(),
          userId: userId ? parseInt(userId) : 1, // 确保userId是数字类型
          userType: MESSAGE_TYPE.USER,
          type: 'file',
          fileName: file.name,
          fileInfo: {
            name: file.name,
            size: file.size,
            type: file.type
          }
        };

        setMessages((prevMessages) => [...prevMessages, newMessage]);

        const newMessageForDB = {
          ...newMessage,
          userId: userId ? parseInt(userId) : 1, // 确保userId是数字类型
          fileInfo: JSON.stringify(newMessage.fileInfo)
        };

        try {
          const saveResponse = await fetch("http://10.98.64.22:8080/message/add", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(newMessageForDB),
          });
          
          if (!saveResponse.ok) {
            console.error('保存用户消息失败:', saveResponse.status);
          }
        } catch (error) {
          console.error('保存用户消息时出错:', error);
        }

        const uploadKey = 'uploadFile';
        message.loading({ content: '正在上传文件...', key: uploadKey });

        uploadTCADFile(formData, {
          headers: {
            'Content-Type': 'multipart/form-data',
          },
          onUploadProgress: function (progressEvent) {
            var percent = (progressEvent.loaded / progressEvent.total) * 100;
          },
        })
          .then((response) => {
            if (response?.status === 200) {
              message.success({ content: `${file.name} 上传成功`, key: uploadKey, duration: 2 });
              onSuccess(response);
              
              const fileUrl = URL.createObjectURL(file);
              
              const uploadMessage = {
                content: `${file.name} 文件上传成功`,
                userType: MESSAGE_TYPE.BOT,
                fileUrl: fileUrl,
                fileName: file.name,
                messageId: maxMessageId + 2,
                modelId: 3,
                sessionId: currentSessionId,
                timestamp: new Date().toISOString(),
                userId: userId ? parseInt(userId) : 1, // 确保userId是数字类型
                type: 'file',
                fileInfo: {
                  name: file.name,
                  fileName: file.name,
                  size: file.size,
                  type: file.type
                }
              };

              setMessages((prevMessages) => [...prevMessages, uploadMessage]);

              const uploadMessageForDB = {
                content: `${file.name} 文件上传成功`,
                messageId: maxMessageId + 2,
                modelId: 3,
                sessionId: currentSessionId,
                timestamp: new Date().toISOString(),
                userId: userId ? parseInt(userId) : 1, // 确保userId是数字类型
                userType: MESSAGE_TYPE.BOT,
                type: 'file',
                fileInfo: JSON.stringify({
                  name: file.name,
                  fileName: file.name,
                  size: file.size,
                  type: file.type,
                  isDeleted: false,
                  deleted: false
                })
              };
              
              fetch("http://10.98.64.22:8080/message/add", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(uploadMessageForDB),
              }).then(botSaveResponse => {
                if (!botSaveResponse.ok) {
                  console.error('保存机器人消息失败:', botSaveResponse.status);
                } else {
                  // 🔧 触发历史更新事件
                  window.sessionUpdated = Date.now();
                  window.dispatchEvent(new Event("sessionUpdated"));
                  
                  // 添加标题更新
                  updateSessionHeader(currentSessionId, userId, `${file.name} 文件上传`, `${file.name} 文件上传成功`);
                }
              }).catch(error => {
                console.error('保存机器人消息时出错:', error);
              });

            } else {
              message.error({ content: `${file.name} 上传失败`, key: uploadKey, duration: 2 });
              onError(response);
              
              delete fileUploadTimesRef.current[file.name];
              
              const uploadMessage = {
                content: `${file.name} 文件上传失败`,
                userType: MESSAGE_TYPE.BOT,
                messageId: maxMessageId + 2,
                modelId: 3,
                sessionId: currentSessionId,
                timestamp: new Date().toISOString(),
                userId: userId ? parseInt(userId) : 1, // 确保userId是数字类型
              };

              setMessages((prevMessages) => [...prevMessages, uploadMessage]);

              fetch("http://10.98.64.22:8080/message/add", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(uploadMessage),
              });
            }
          })
          .catch(function (error) {
            message.error({ content: `${file.name} 上传过程中出错`, key: uploadKey, duration: 2 });
            console.error('Upload error:', error);
            onError(error);
            
            delete fileUploadTimesRef.current[file.name];

            const errorMessage = {
              content: `${file.name} 文件上传过程中出错，请稍后重试`,
              userType: MESSAGE_TYPE.BOT,
              messageId: maxMessageId + 2,
              modelId: 3,
              sessionId: currentSessionId,
              timestamp: new Date().toISOString(),
              userId: userId ? parseInt(userId) : 1, // 确保userId是数字类型
            };

            setMessages((prevMessages) => [...prevMessages, errorMessage]);

            fetch("http://10.98.64.22:8080/message/add", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify(errorMessage),
            });
          })
          .finally(() => {
            setUploading(false);
          });
      } catch (error) {
        console.error("文件上传过程中出错:", error);
        message.error("文件上传前准备工作出错，请稍后重试");
        onError(error);
        setUploading(false);
      }
    },
    [sessionId, userId, currentRagConfig],
  );

  const uploadProps = useMemo(() => {
    return {
      listType: 'picture',
      multiple: false,
      showUploadList: false,
      beforeUpload,
      customRequest: ({ file, onSuccess, onError }) => onUploadFile(file, onSuccess, onError),
      onChange(info) {
        const { file } = info;
        const { status } = file;

        if (status === 'uploading') {
          setUploading(true);
          setFileList([...info.fileList]);
        }
        if (status === 'done') {
          setUploading(false);
        } else if (status === 'error') {
          setUploading(false);
        }
      },
    };
  }, [onUploadFile]);

  // 中止流式输出处理函数
  const handleAbortStream = useCallback(() => {
    if (streaming && streamRequestRef.current) {
      message.loading({ content: '正在中止...', key: 'abortMessage' });
      
      streamRequestRef.current.cancel().then(() => {
        // 中止请求已发送
        setStreaming(false);
        setLoading(false);
        message.success({ content: '已成功中止回答', key: 'abortMessage' });
      }).catch(error => {
        console.error('中止过程中出错:', error);
        message.error({ content: '中止过程中出错，但已停止显示', key: 'abortMessage' });
      });
    }
  }, [streaming]);

  const handleRagConfigSwitch = async (key) => {
    try {
      if (streaming) {
        message.warning('请等待当前回答完成后再切换知识库');
        return;
      }
      
      setSwitchingRag(true);
      
      const previousConfig = currentRagConfig;
      
      if (key === 'none') {
        setCurrentRagConfig('none');
        currentRagConfigRef.current = 'none';
        
        localStorage.setItem(`ragConfig_${sessionId}`, 'none');
        
        message.loading({ content: '正在禁用知识库功能...', key: 'switchRag' });
        
        try {
          const response = await fetch(`http://10.98.64.22:5100/set_active_configuration`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ config_id: 'none' })
          });
          
          if (response.ok) {
            if (previousConfig !== 'none') {
              const messageListResponse = await fetch("http://10.98.64.22:8080/message/list-all");
              const allMessages = await messageListResponse.json();
              const maxMessageId = allMessages.length ? Math.max(...allMessages.map(msg => msg.messageId)) : 0;
              
              const switchMessage = {
                content: `已禁用知识库功能`,
                messageId: maxMessageId + 1,
                modelId: 3,
                userType: MESSAGE_TYPE.BOT,
                timestamp: new Date().toISOString(),
                sessionId: sessionId,
                userId: userId ? parseInt(userId) : 1, // 确保userId是数字类型
                isSystemPrompt: true
              };
              
              setMessages(prev => [...prev, switchMessage]);
              
              await fetch("http://10.98.64.22:8080/message/add", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(switchMessage),
              });
              
              message.success({ content: `已禁用知识库功能`, key: 'switchRag' });
            } else {
              message.info({ content: '知识库设置未发生变化', key: 'switchRag' });
            }
          } else {
            setCurrentRagConfig(previousConfig);
            currentRagConfigRef.current = previousConfig;
            message.error({ content: '禁用知识库失败，请稍后重试', key: 'switchRag' });
          }
        } catch (error) {
          console.error('禁用知识库出错:', error);
          setCurrentRagConfig(previousConfig);
          currentRagConfigRef.current = previousConfig;
          message.error({ content: '禁用知识库出错，请稍后重试', key: 'switchRag' });
        }
        
        setSwitchingRag(false);
        return;
      }
      
      message.loading({ content: '正在切换知识库...', key: 'switchRag' });
      
      setCurrentRagConfig(key);
      currentRagConfigRef.current = key;

      localStorage.setItem(`ragConfig_${sessionId}`, key);
      
      const response = await fetch(`http://10.98.64.22:5100/set_active_configuration`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ config_id: key })
      });
      
      if (response.ok) {
        const newConfig = ragConfigurations.find(c => c.id === key);
        if (newConfig && previousConfig !== key) {
          const messageListResponse = await fetch("http://10.98.64.22:8080/message/list-all");
          const allMessages = await messageListResponse.json();
          const maxMessageId = allMessages.length ? Math.max(...allMessages.map(msg => msg.messageId)) : 0;
          
          const switchMessage = {
            content: `已切换知识库到: ${newConfig.name}`,
            messageId: maxMessageId + 1,
            modelId: 3,
            userType: MESSAGE_TYPE.BOT,
            timestamp: new Date().toISOString(),
            sessionId: sessionId,
            userId: userId ? parseInt(userId) : 1, // 确保userId是数字类型
            isSystemPrompt: true
          };
          
          setMessages(prev => [...prev, switchMessage]);
          
          await fetch("http://10.98.64.22:8080/message/add", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(switchMessage),
          });
          
          message.success({ content: `已切换到知识库: ${newConfig.name}`, key: 'switchRag' });
        } else {
          message.info({ content: '知识库未发生变化', key: 'switchRag' });
        }
      } else {
        setCurrentRagConfig(previousConfig);
        currentRagConfigRef.current = previousConfig;
        message.error({ content: '切换知识库失败，请稍后重试', key: 'switchRag' });
      }
    } catch (error) {
      console.error('设置知识库配置出错:', error);
      message.error({ content: '切换知识库出错，请稍后重试', key: 'switchRag' });
    } finally {
      setSwitchingRag(false);
    }
  };

  return (
    <div className='tcad'>
      {!messages.length && (
        <div className='tcad-empty'>
          <div className='tcad-title'>您好，我是TCAD大模型</div>
          <div className='tcad-question'>有什么相关问题吗？</div>
          <div className='tcad-intro'>支持对Sentaurus TCAD自动仿真、结果分析和优化指导</div>
          <div className='tcad-intro' style={{ marginTop: -10 }}>请上传器件的sde和sdevice文件，并声明所需的参数</div>
        </div>
      )}
      {messages.length > 0 && (
        <div className="chat-message-list">
          {messages.map((item, index) => (
              <div key={index} className="chat-message-item">
                <ChatMessage
                  sendType={item.userType}
                  message={item.content}
                  loading={item.loading}
                  streaming={item.streaming}
                  type={item.type || (item.fileUrl ? 'file' : undefined)}
                  fileInfo={item.fileInfo || (item.fileName ? {
                    name: item.fileName,
                    fileName: item.fileName,
                    isDeleted: item.isDeleted || item.deleted,
                    deleted: item.isDeleted || item.deleted
                  } : undefined)}
                  downloadUrl={item.fileUrl}
                  messageId={item.messageId || index}
                  // onFileDelete={item.userType === MESSAGE_TYPE.USER ? handleFileDelete : undefined} // 注释掉文件撤回功能
                  isDeleted={item.isDeleted}
                  deleted={item.deleted}
                  isSystemPrompt={item.isSystemPrompt}
                />
              </div>
            ))}
        </div>
      )}
      <div className='tcad-footer'>
        <div style={{ display: 'flex', width: '100%', alignItems: 'center' }}>
          <Form
            form={form}
            layout='inline'
            style={{ flex: 1, display: 'flex', alignItems: 'center' }}
            onFinish={onhandleFinished}
            autoComplete='off'
          >
            <Form.Item name='content' style={{ flex: 1, margin: '0 10px 0 0' }}>
              <Input 
                placeholder='尽管问...' 
                disabled={loading || streaming || uploading || switchingRag}
              />
            </Form.Item>
            
            <Upload {...uploadProps} fileList={fileList} disabled={loading || streaming || switchingRag}>
              <Button disabled={loading || streaming || uploading || switchingRag}>
                {uploading ? <LoadingOutlined /> : <CloudUploadOutlined />}
              </Button>
            </Upload>

            <div className='devide-line'></div>
            <Form.Item>
              {streaming ? (
                <Button
                  danger
                  onClick={handleAbortStream}
                  icon={<StopOutlined style={{ fontSize: '18px' }} />}
                >
                  中止
                </Button>
              ) : (
                <Button
                  disabled={loading || uploading || switchingRag}
                  loading={loading}
                  htmlType='submit'
                  icon={
                    <img
                      src={require('../../assets/send.png')}
                      style={{ height: 32, width: 32 }}
                      alt="发送"
                    ></img>
                  }
                ></Button>
              )}
            </Form.Item>
          </Form>

          <div className="rag-selector" style={{ 
            marginLeft: '16px', 
            display: 'flex', 
            alignItems: 'center'
          }}>
            <Dropdown
              menu={{
                items: [
                  ...ragConfigurations.map(config => ({
                    key: config.id,
                    label: (
                      <span>
                        {config.name}
                        {config.id === currentRagConfig && <span style={{ marginLeft: 8 }}>✓</span>}
                      </span>
                    ),
                    disabled: streaming || switchingRag,
                  })),
                  { type: 'divider' },
                  {
                    key: 'manage',
                    icon: <DatabaseOutlined />,
                    label: '管理知识库',
                    disabled: streaming || switchingRag,
                  },
                ],
                onClick: async ({ key }) => {
                  if (key === 'manage') {
                    if (streaming || switchingRag) {
                      message.warning('请等待当前操作完成后再管理知识库');
                      return;
                    }
                    dispatch(updateMainPage({ Main_Page: 'RagManager' }));
                  } else {
                    handleRagConfigSwitch(key);
                  }
                },    
              }}
              trigger={['click']}
              disabled={streaming || switchingRag}
            >
              <Button
                type="default"
                size="middle"
                icon={<DatabaseOutlined />}
                loading={switchingRag}
                disabled={streaming || switchingRag}
                style={{ 
                  height: '32px',
                  display: 'flex',
                  alignItems: 'center'
                }}
              >
                {switchingRag 
                  ? '切换中...' 
                  : (currentRagConfig === 'none'
                      ? '无'
                      : (currentRagConfig
                          ? ragConfigurations.find(c => c.id === currentRagConfig)?.name
                          : '知识库'))
                }
              </Button>
            </Dropdown>
          </div>
        </div>
      </div>
    </div>
  );
};

export default Chatbot;