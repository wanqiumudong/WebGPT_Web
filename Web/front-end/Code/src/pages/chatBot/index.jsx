import './index.css';
import React, { useEffect, useMemo, useCallback, useState, useRef } from 'react';
import { Button, Form, Input, message, Upload } from 'antd';
import Cookies from 'js-cookie';
import { fetchChatBot, fetchChatBotStreaming, uploadFile } from '../../api/chatBot';
import { MESSAGE_TYPE, THINKING_TEXTS } from '../../constants';
import ChatMessage from '../../components/chatMessage';
import { botInfo } from '../../constants';
import { LoadingOutlined, CloudUploadOutlined, StopOutlined } from '@ant-design/icons';
import { DatabaseOutlined } from '@ant-design/icons';
import { Dropdown, Menu, Tooltip } from 'antd';
import { useDispatch } from 'react-redux';
import { updateMainPage } from '../../store/pageStore';
import { DEFAULT_SESSION, createRealSessionAfterChat, isDefaultSession } from '../../components/history/history';
import { BACKEND_BASE_URL, RAG_MANAGER_BASE_URL } from '../../config/endpoints';
import { normalizeIdentity, resolveCurrentUserId } from '../../utils/userIdentity';

const Chatbot = ({ port }) => {
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [messages, setMessages] = useState([]);
  const username = Cookies.get('user');
  const userId = normalizeIdentity(Cookies.get('userid')) || normalizeIdentity(Cookies.get('userId'));
  const effectiveUserId = useMemo(
    () => resolveCurrentUserId({ preferredUserId: userId, preferredUsername: username }),
    [userId, username]
  );
  const persistedUserId = useMemo(
    () => normalizeIdentity(userId) || effectiveUserId,
    [effectiveUserId, userId]
  );
  const [fileList, setFileList] = useState([]);
  const [sessionId, setSessionId] = useState(
    () => normalizeIdentity(Cookies.get('chatbot_sessionId')) || normalizeIdentity(Cookies.get(5)) || DEFAULT_SESSION
  );
  const [streamConnection, setStreamConnection] = useState(null);
  
  const [dynamicThinkingText, setDynamicThinkingText] = useState('思考中...');
  const thinkingIntervalRef = useRef(null);
  const streamRequestRef = useRef(null);
  
  const [ragConfigurations, setRagConfigurations] = useState([]);
  const [currentRagConfig, setCurrentRagConfig] = useState(null);
  const [switchingRag, setSwitchingRag] = useState(false);
  const dispatch = useDispatch();

  const API_BASE_URL = BACKEND_BASE_URL;
  const RAG_API_BASE_URL = RAG_MANAGER_BASE_URL;

  const getChatbotSessionCookie = useCallback(
    () => normalizeIdentity(Cookies.get('chatbot_sessionId')) || normalizeIdentity(Cookies.get(5)) || DEFAULT_SESSION,
    []
  );

  const buildClientMessageId = useCallback(
    () => `chatbot-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`,
    []
  );

  const ensureActiveSessionId = useCallback(
    async (candidateSessionId = sessionId) => {
      let currentSessionId = normalizeIdentity(candidateSessionId) || getChatbotSessionCookie();

      if (!currentSessionId || currentSessionId === DEFAULT_SESSION) {
        const newSessionId = await createRealSessionAfterChat(0);
        if (!newSessionId) {
          throw new Error('创建会话失败');
        }
        currentSessionId = newSessionId;
      } else {
        try {
          const response = await fetch(`${API_BASE_URL}/session/get?sessionId=${encodeURIComponent(currentSessionId)}`);
          if (!response.ok) {
            const newSessionId = await createRealSessionAfterChat(0);
            if (!newSessionId) {
              throw new Error('创建会话失败');
            }
            currentSessionId = newSessionId;
          }
        } catch (error) {
          const newSessionId = await createRealSessionAfterChat(0);
          if (!newSessionId) {
            throw new Error('创建会话失败');
          }
          currentSessionId = newSessionId;
        }
      }

      setSessionId(currentSessionId);
      Cookies.set('chatbot_sessionId', currentSessionId, { expires: 7 });
      Cookies.set(5, currentSessionId, { expires: 7 });
      return currentSessionId;
    },
    [API_BASE_URL, getChatbotSessionCookie, sessionId]
  );
  
  const startThinkingAnimation = useCallback(() => {
    if (thinkingIntervalRef.current) return;
    
    let textIndex = 0;
    thinkingIntervalRef.current = setInterval(() => {
      setDynamicThinkingText(THINKING_TEXTS[textIndex]);
      textIndex = (textIndex + 1) % THINKING_TEXTS.length;
    }, 500);
  }, []);
  
  const stopThinkingAnimation = useCallback(() => {
    if (thinkingIntervalRef.current) {
      clearInterval(thinkingIntervalRef.current);
      thinkingIntervalRef.current = null;
    }
    setDynamicThinkingText('思考中...');
  }, []);
  
  const handleRagConfigSwitch = useCallback(async (key) => {
    try {
      if (streaming) {
        message.warning('请等待当前回答完成后再切换知识库');
        return;
      }
      
      setSwitchingRag(true);
      message.loading({ content: '正在切换知识库...', key: 'switchRag' });
      
      const previousConfig = currentRagConfig;
      
      if (key === 'none') {
        message.loading({ content: '正在禁用知识库功能...', key: 'switchRag' });
        setCurrentRagConfig('none');
        localStorage.setItem(`ragConfig_${sessionId}`, 'none');
        
        try {
          const response = await fetch(`${RAG_API_BASE_URL}/set_active_configuration`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ config_id: 'none', user_id: effectiveUserId })
          });          
          
          if (previousConfig !== 'none') {
            const switchMessage = {
              content: "已禁用知识库功能",
              messageId: buildClientMessageId(),
              modelId: 0,
              userType: MESSAGE_TYPE.BOT,
              timestamp: new Date().toISOString(),
              sessionId: sessionId,
              userId: persistedUserId
            };

            setMessages(prev => [...prev, switchMessage]);

            fetch(`${API_BASE_URL}/message/add`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify(switchMessage),
            }).catch(error => console.error('保存知识库切换消息失败:', error));
            
            message.success({ content: '已禁用知识库功能', key: 'switchRag' });
          } else {
            message.info({ content: '知识库设置未发生变化', key: 'switchRag' });
          }
        } catch (error) {
          message.error({ content: '禁用知识库出错,请稍后重试', key: 'switchRag' });
          setCurrentRagConfig(previousConfig);
        }
        setSwitchingRag(false);
        return;
      }
      
      const response = await fetch(`${RAG_API_BASE_URL}/set_active_configuration`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ config_id: key, user_id: effectiveUserId })
      });      
      
      if (response.ok) {
        setCurrentRagConfig(key);
        localStorage.setItem(`ragConfig_${sessionId}`, key);
        
        const newConfig = ragConfigurations.find(c => c.id === key);
        if (newConfig && previousConfig !== key) {
          const switchMessage = {
            content: `已切换知识库到: ${newConfig.name}`,
            messageId: buildClientMessageId(),
            modelId: 0,
            userType: MESSAGE_TYPE.BOT,
            timestamp: new Date().toISOString(),
            sessionId: sessionId,
            userId: persistedUserId
          };

          setMessages(prev => [...prev, switchMessage]);

          fetch(`${API_BASE_URL}/message/add`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(switchMessage),
          }).catch(error => console.error('保存知识库切换消息失败:', error));
          
          message.success({ content: `已切换到知识库: ${newConfig.name}`, key: 'switchRag' });
        } else {
          message.info({ content: '知识库未发生变化', key: 'switchRag' });
        }
      } else {
        setCurrentRagConfig(previousConfig);
        message.error({ content: '切换知识库失败,请稍后重试', key: 'switchRag' });
      }
    } catch (error) {
      message.error({ content: '切换知识库出错,请稍后重试', key: 'switchRag' });
    } finally {
      setSwitchingRag(false);
    }
  }, [API_BASE_URL, buildClientMessageId, currentRagConfig, effectiveUserId, persistedUserId, port, ragConfigurations, sessionId, streaming]);
  
  const fetchRagConfigurations = useCallback(async () => {
    try {
      const savedConfig = localStorage.getItem(`ragConfig_${sessionId}`);
      const response = await fetch(`${RAG_API_BASE_URL}/get_rag_configurations?user_id=${encodeURIComponent(effectiveUserId)}`);
      
      if (response.ok) {
        const data = await response.json();
        
        if (Array.isArray(data.configurations) && data.configurations.length > 0) {
          setRagConfigurations(data.configurations);
          
          if (savedConfig === 'none') {
            setCurrentRagConfig('none');
            return;
          }
          
          if (savedConfig && data.configurations.some(c => c.id === savedConfig)) {
            setCurrentRagConfig(savedConfig);
            return;
          }
          
          const activeConfig = data.configurations.find(c => c.active);
          if (activeConfig) {
            setCurrentRagConfig(activeConfig.id);
          } else if (data.configurations.length > 0) {
            setCurrentRagConfig(data.configurations[0].id);
          }
        } else {
          setRagConfigurations([]);
        }
      }
    } catch (error) {
      setRagConfigurations([]);
    }
  }, [sessionId, effectiveUserId]);
  
  const beforeUpload = (file) => {
    setFileList([]);
    if (file.size / 1024 / 1024 > 5) {
      message.error("文件大小限制在5MB以内");
      return Upload.LIST_IGNORE;
    }
    return true;
  };
  
  const onUploadFile = useCallback(
    async (file, onSuccess, onError) => {
      try {
        const currentSessionId = await ensureActiveSessionId(sessionId);

        var formData = new FormData();
        formData.append('file', file);
        formData.append('type', file.type);
        formData.append('conversation_id', currentSessionId);
        formData.append('user_id', username);
        
      uploadFile(formData, {
        headers: {
          'Content-Type': 'multipart/form-data',
        },
        onUploadProgress: function (progressEvent) {
          var percent = (progressEvent.loaded / progressEvent.total) * 100;
        },
      }, port)
        .then((response) => {
          if (response?.status === 200) {
            onSuccess(response);
            
            if (response.data && response.data.content) {
              const botResponseMessage = {
                content: response.data.content,
                messageId: buildClientMessageId(),
                modelId: 0,
                sessionId: currentSessionId,
                timestamp: new Date().toISOString(),
                userId: persistedUserId,
                userType: MESSAGE_TYPE.BOT,
              };

              setMessages(prevMessages => [...prevMessages, botResponseMessage]);

              fetch(`${API_BASE_URL}/message/add`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(botResponseMessage),
              }).catch(err => console.error('保存机器人图片响应消息失败:', err));
            }
          } else {
            onError(response);
          }
        })
        .catch(function (error) {
          onError(error);
        });
      } catch (error) {
        onError(error);
      }
    },
    [API_BASE_URL, buildClientMessageId, ensureActiveSessionId, persistedUserId, port, sessionId, username]
  );
  
  const uploadProps = useMemo(() => {
    return {
      listType: 'picture',
      multiple: true,
      showUploadList: false,
      beforeUpload,
      customRequest: ({ file, onSuccess, onError }) => onUploadFile(file, onSuccess, onError),
      onChange(info) {
        const { file } = info;
        const { status } = file;
        if (status === 'uploading') {
          setUploading(true);
          setFileList([...info.fileList]);
          const fileList = info.fileList;
          const fileNames = fileList.map(item => item.name);
          const messageList = messages.slice();
          messageList.push({
            content: `${fileNames.join('、')}文件上传`,
            userType: MESSAGE_TYPE.USER
          });
          setMessages(messageList);
        }
        if (status === 'done') {
          setUploading(false);
          message.success(`${info.file.name} 上传成功`);
        } else if (status === 'error') {
          setUploading(false);
          message.error(`${info.file.name} 上传失败`);
        }
      },
    };
  }, [messages, onUploadFile]);
  
  const handleAbortStream = useCallback(() => {
    if (streaming && streamRequestRef.current) {
      message.loading({ content: '正在中止...', key: 'abortMessage' });
      
      streamRequestRef.current.cancel().then(() => {
        setStreaming(false);
        setLoading(false);
        message.success({ content: '已成功中止回答', key: 'abortMessage' });
      }).catch(error => {
        message.error({ content: '中止过程中出错,但已停止显示', key: 'abortMessage' });
      });
    }
  }, [streaming]);
  
  const onhandleFinished = async () => {
    const values = await form.getFieldsValue();
    const content = values?.content?.trim();
    if (!content) return;
    
    setLoading(true);
    setStreaming(true);
    
    let actualSessionId;
    try {
      actualSessionId = await ensureActiveSessionId(sessionId);
    } catch (error) {
      setLoading(false);
      setStreaming(false);
      message.error('创建会话失败');
      return;
    }
    
    const newMessage = {
      content,
      messageId: buildClientMessageId(),
      modelId: 0,
      sessionId: actualSessionId,
      timestamp: new Date().toISOString(),
      userId: persistedUserId,
      userType: MESSAGE_TYPE.USER,
    };
    
    const loadingText = dynamicThinkingText;
    
    const tempBotMessage = {
      content: loadingText,
      messageId: buildClientMessageId(),
      modelId: 0,
      sessionId: actualSessionId,
      timestamp: new Date().toISOString(),
      userId: persistedUserId,
      userType: MESSAGE_TYPE.BOT,
      streaming: true,
      isLoading: true
    };
    
    const currentMessages = Array.isArray(messages) ? messages : [];
    const submitMessages = [...currentMessages, newMessage, tempBotMessage];

    setMessages(submitMessages);
    await form.resetFields();

    fetch(`${API_BASE_URL}/message/add`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(newMessage),
    }).catch(error => console.error('保存用户消息时出错:', error));
    
    startThinkingAnimation();
    
    const params = {
      user_id: effectiveUserId,
      message: content,
      conversation_id: actualSessionId,
      config_id: currentRagConfig === 'none' ? 'none' : (currentRagConfig || 'default')
    };
    
    try {
      let fullResponse = '';
      let abortedLocally = false;
      
      streamRequestRef.current = fetchChatBotStreaming(
        params, 
        (data) => {
          if (data) {
            if (data.start_streaming) {
              setMessages(prevMessages => {
                const updatedMessages = [...prevMessages];
                const lastIndex = updatedMessages.length - 1;
                
                if (lastIndex >= 0 && updatedMessages[lastIndex].streaming) {
                  updatedMessages[lastIndex] = {
                    ...updatedMessages[lastIndex],
                    request_id: data.request_id,
                    content: "",
                    streaming: true,
                    isLoading: false
                  };
                }
                return updatedMessages;
              });
              return;
            }
            
            if (data.aborted) {
              abortedLocally = true;
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
          
          stopThinkingAnimation();

          if (abortedLocally) {
            return;
          }
          
          const finalBotMessage = {
            content: fullResponse,
            messageId: tempBotMessage.messageId,
            modelId: 0,
            sessionId: actualSessionId,
            timestamp: new Date().toISOString(),
            userId: persistedUserId,
            userType: MESSAGE_TYPE.BOT,
          };
          
          try {
            const finalResponse = await fetch(`${API_BASE_URL}/message/add`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify(finalBotMessage),
            });
          } catch (error) {
            console.error('保存机器人回复时出错:', error);
          }
          
          setMessages(prev => {
            const updatedMessages = [...prev];
            const lastIndex = updatedMessages.length - 1;
            
            if (lastIndex >= 0 && updatedMessages[lastIndex].streaming) {
                updatedMessages[lastIndex] = {
                    ...updatedMessages[lastIndex],
                    streaming: false,
                    isLoading: false
                };
            }
            return updatedMessages;
          });
          
          // 更新会话标题
          try {
            if (!actualSessionId) {
              return;
            }
            
            const sessionResponse = await fetch(`${API_BASE_URL}/session/get?sessionId=${actualSessionId}`);
            if (sessionResponse.ok) {
              const currentSession = await sessionResponse.json();
              if (currentSession && currentSession.header && currentSession.header !== '新会话') {
                return;
              }
            }
            
            const response = await fetch(`${API_BASE_URL}/message/list-by-session?sessionId=${actualSessionId}`);
            
            if (!response.ok) {
              return;
            }
            
            const messagesList = await response.json();
            
            if (!Array.isArray(messagesList)) {
              return;
            }
            
            const firstUserMessage = messagesList.find(m => m.userType === MESSAGE_TYPE.USER);
            const firstBotMessage = messagesList.find(m => m.userType === MESSAGE_TYPE.BOT);
            
            if (firstUserMessage) {
              let generatedTitle = '';
              
              // 直接使用简单的标题生成策略，不调用AI API
              // 检查是否是图片上传场景
              if (firstUserMessage.content.includes('<img') || firstUserMessage.content.includes('src=')) {
                generatedTitle = '图片分析';
              } else {
                // 使用用户输入的前8个字符，过滤HTML标签
                const cleanContent = firstUserMessage.content.replace(/<[^>]*>/g, '');
                generatedTitle = cleanContent.slice(0, 8) || '新对话';
              }
              
              const headerUpdate = {
                createTime: new Date().toISOString(),
                header: generatedTitle || '新对话',
                lastActive: new Date().toISOString(),
                modelId: 0,
                sessionId: actualSessionId,
                status: 1,
                userId: persistedUserId,
              };
              
              await fetch(`${API_BASE_URL}/session/update`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(headerUpdate),
              }).then(response => {
                if (response.ok) {
                  window.sessionUpdated = Date.now();
                  window.dispatchEvent(new Event("sessionUpdated"));
                }
              });
            }
          } catch (error) {
            console.error("更新会话标题时出错:", error);
          }
        },
        (error) => {
          setStreaming(false);
          setLoading(false);
          
          stopThinkingAnimation();
          
          message.error("请求出错,请稍后重试");
          
          setMessages(prev => {
            return prev.filter(msg => !msg.streaming);
          });
        }
      );
      
    } catch (e) {
      setStreaming(false);
      setLoading(false);
      
      stopThinkingAnimation();
      
      setMessages(prev => prev.filter(msg => !msg.streaming && !msg.isLoading));
      message.error("请求出错,请稍后重试");
    }
  };
  
  useEffect(() => {
    const handleSessionChange = () => {
      const newSessionId = getChatbotSessionCookie();
      if (newSessionId !== sessionId) {
        setSessionId(newSessionId);
      }
    };
    const interval = setInterval(handleSessionChange, 1000);
    return () => clearInterval(interval);
  }, [getChatbotSessionCookie, sessionId]);
  
  useEffect(() => {
    if (sessionId && sessionId !== DEFAULT_SESSION) {
      fetch(`${API_BASE_URL}/message/list-by-session?sessionId=${sessionId}`)
        .then(response => {
          if (!response.ok) {
            throw new Error(`状态码: ${response.status}`);
          }
          return response.json();
        })
        .then(data => {
          const messagesData = Array.isArray(data) ? data : [];
          
          const formattedMessages = messagesData.map(msg => ({
            ...msg,
            streaming: false,
            isLoading: false
          }));
          
          setMessages(prevMessages => {
            if (Array.isArray(prevMessages) && prevMessages.some(msg => msg.streaming)) {
              return prevMessages;
            }
            return formattedMessages;
          });
        })
        .catch(error => {
          setMessages([]);
        });
    } else if (sessionId === DEFAULT_SESSION) {
      setMessages([]);
    } else {
      setMessages([]);
    }
  }, [API_BASE_URL, sessionId]);
  
  useEffect(() => {
    fetchRagConfigurations();
  }, [fetchRagConfigurations]);
  
  useEffect(() => {
    return () => {
      stopThinkingAnimation();
    };
  }, [stopThinkingAnimation]);
  
  return (
    <div className='chat'>
      {!messages.length && (
        <div className='chat-empty'>
          <div className='chat-title'>您好,我是智能助手</div>
          <div className='chat-question'>有什么相关问题吗?</div>
          <div className='chat-info'></div>
        </div>
      )}
      
      {messages.length > 0 && (
        <div className="chat-message-container">
          <div className="chat-message-list">
            {messages.map((item, index) => {
              return (
                <div
                  key={`message-${index}-${item.messageId || index}`}
                  className="chatbot-message"
                >
                  <ChatMessage
                    sendType={item.userType}
                    message={item.content}
                    loading={item.streaming && item.isLoading}
                    streaming={item.streaming}
                    messageId={item.messageId}
                  />
                </div>
              );
            })}
          </div>
        </div>
      )}
      
      <div className='chat-footer'>
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
                    />
                  }
                />
              )}
            </Form.Item>
          </Form>
          
          <div className="rag-selector" style={{ 
            marginLeft: '10px', 
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
                          ? ragConfigurations.find(c => c.id === currentRagConfig)?.name || '知识库'
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
