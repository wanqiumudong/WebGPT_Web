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

const Chatbot = ({ port }) => {
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [messages, setMessages] = useState([]);
  const username = Cookies.get('user');
  const userId = Cookies.get('userid') || Cookies.get('userId');
  const [fileList, setFileList] = useState([]);
  const [sessionId, setSessionId] = useState(Cookies.get(5));
  const [streamConnection, setStreamConnection] = useState(null);
  
  const [dynamicThinkingText, setDynamicThinkingText] = useState('思考中...');
  const thinkingIntervalRef = useRef(null);
  const streamRequestRef = useRef(null);
  
  const [ragConfigurations, setRagConfigurations] = useState([]);
  const [currentRagConfig, setCurrentRagConfig] = useState(null);
  const [switchingRag, setSwitchingRag] = useState(false);
  const dispatch = useDispatch();
  
  // 随机选择Chatbot端口函数
  const getRandomChatbotPort = () => {
    const chatbotPorts = [5002, 5012, 5013, 5022];
    const randomPort = chatbotPorts[Math.floor(Math.random() * chatbotPorts.length)];
    return randomPort;
  };
  
  const API_BASE_URL = `http://10.98.64.22:8080`;
  
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
          let response = await fetch(`http://localhost:5100/set_active_configuration`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ config_id: 'none' })
          });
          
          if (!response.ok) {
            response = await fetch(`http://localhost:5100/set_active_configuration`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ config_id: 'none' })
            });
          }
          
          if (previousConfig !== 'none') {
            const switchMessage = {
              content: "已禁用知识库功能",
              modelId: 0,
              userType: MESSAGE_TYPE.BOT,
              timestamp: new Date().toISOString(),
              sessionId: sessionId,
              userId: userId
            };
            
            const messageListResponse = await fetch(`${API_BASE_URL}/message/list-all`);
            if (messageListResponse.ok) {
              const allMessages = await messageListResponse.json();
              const maxMessageId = allMessages?.length ? Math.max(...allMessages.map(msg => msg.messageId)) : 0;
              switchMessage.messageId = maxMessageId + 1;
            }
            
            setMessages(prev => [...prev, switchMessage]);
            
            await fetch(`${API_BASE_URL}/message/add`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify(switchMessage),
            });
            
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
      
      let response = await fetch(`http://localhost:5100/set_active_configuration`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ config_id: key })
      });
      
      if (!response.ok) {
        response = await fetch(`http://localhost:5100/set_active_configuration`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ config_id: key })
        });
      }
      
      if (response.ok) {
        setCurrentRagConfig(key);
        localStorage.setItem(`ragConfig_${sessionId}`, key);
        
        const newConfig = ragConfigurations.find(c => c.id === key);
        if (newConfig && previousConfig !== key) {
          const switchMessage = {
            content: `已切换知识库到: ${newConfig.name}`,
            modelId: 0,
            userType: MESSAGE_TYPE.BOT,
            timestamp: new Date().toISOString(),
            sessionId: sessionId,
            userId: userId
          };
          
          const messageListResponse = await fetch(`${API_BASE_URL}/message/list-all`);
          if (messageListResponse.ok) {
            const allMessages = await messageListResponse.json();
            const maxMessageId = allMessages?.length ? Math.max(...allMessages.map(msg => msg.messageId)) : 0;
            switchMessage.messageId = maxMessageId + 1;
          }
          
          setMessages(prev => [...prev, switchMessage]);
          
          await fetch(`${API_BASE_URL}/message/add`, {
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
        message.error({ content: '切换知识库失败,请稍后重试', key: 'switchRag' });
      }
    } catch (error) {
      message.error({ content: '切换知识库出错,请稍后重试', key: 'switchRag' });
    } finally {
      setSwitchingRag(false);
    }
  }, [streaming, currentRagConfig, sessionId, userId, port, ragConfigurations]);
  
  const fetchRagConfigurations = useCallback(async () => {
    try {
      const savedConfig = localStorage.getItem(`ragConfig_${sessionId}`);
      
      // 尝试通过Chatbot服务获取RAG配置(支持负载均衡)
      const selectedPort = getRandomChatbotPort();
      let response;
      
      try {
        // 优先尝试通过负载均衡器获取配置
        response = await fetch(`http://10.98.64.22:5100/get_rag_configurations?user_id=${userId}`);
      } catch (error) {
        // 如果负载均衡器不可用，回退到直接访问RAG Manager
        response = await fetch(`http://10.98.64.22:5100/get_rag_configurations?user_id=${userId}`);
      }
      
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
  }, [sessionId, getRandomChatbotPort]);
  
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
        let currentSessionId = Cookies.get('chatbot_sessionId');

        if (currentSessionId && currentSessionId !== sessionId) {
          setSessionId(currentSessionId);
        }

        if (currentSessionId === DEFAULT_SESSION) {
          const newSessionId = await createRealSessionAfterChat(1);
          if (newSessionId) {
            currentSessionId = newSessionId;
            setSessionId(newSessionId);
            Cookies.set('chatbot_sessionId', newSessionId, { expires: 7 });
          } else {
            message.error('创建会话失败');
            onError(new Error('创建会话失败'));
            return;
          }
        }

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
              fetch(`${API_BASE_URL}/message/list-all`)
                .then(res => res.json())
                .then(allMessages => {
                  const maxMessageId = allMessages?.length ? Math.max(...allMessages.map(msg => msg.messageId)) : 0;
                  
                  const botResponseMessage = {
                    content: response.data.content,
                    messageId: maxMessageId + 1,
                    modelId: 0,
                    sessionId: currentSessionId,
                    timestamp: new Date().toISOString(),
                    userId: userId,
                    userType: MESSAGE_TYPE.BOT,
                  };
                  
                  setMessages(prevMessages => [...prevMessages, botResponseMessage]);
                  
                  fetch(`${API_BASE_URL}/message/add`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(botResponseMessage),
                  }).catch(err => console.error('保存机器人图片响应消息失败:', err));
                })
                .catch(err => {
                  const botResponseMessage = {
                    content: response.data.content,
                    userType: MESSAGE_TYPE.BOT,
                  };
                  setMessages(prevMessages => [...prevMessages, botResponseMessage]);
                });
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
    [sessionId, username, port, setSessionId, setMessages, userId]
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
    if (!values?.content) return;
    
    setLoading(true);
    setStreaming(true);
    
    let actualSessionId = sessionId;
    let isInDefaultSession = sessionId === DEFAULT_SESSION;
    
    if (isInDefaultSession) {
      const newSessionId = await createRealSessionAfterChat(0);
      if (newSessionId) {
        actualSessionId = newSessionId;
        setSessionId(newSessionId);
      } else {
        setLoading(false);
        setStreaming(false);
        message.error('创建会话失败');
        return;
      }
    }
    
    const messageListResponse = await fetch(`${API_BASE_URL}/message/list-all`);
    const allMessages = await messageListResponse.json();
    const maxMessageId = allMessages.length ? Math.max(...allMessages.map(msg => msg.messageId)) : 0;
    
    const newMessage = {
      content: values.content,
      messageId: maxMessageId + 1,
      modelId: 0,
      sessionId: actualSessionId,
      timestamp: new Date().toISOString(),
      userId: userId,
      userType: MESSAGE_TYPE.USER,
    };
    
    const loadingText = dynamicThinkingText;
    
    const tempBotMessage = {
      content: loadingText,
      messageId: maxMessageId + 2,
      modelId: 0,
      sessionId: actualSessionId,
      timestamp: new Date().toISOString(),
      userId: userId,
      userType: MESSAGE_TYPE.BOT,
      streaming: true,
      isLoading: true
    };
    
    const currentMessages = Array.isArray(messages) ? messages : [];
    const submitMessages = [...currentMessages, newMessage, tempBotMessage];
    
    await fetch(`${API_BASE_URL}/message/add`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(newMessage),
    });
    
    setMessages(submitMessages);
    await form.resetFields();
    
    startThinkingAnimation();
    
    const params = {
      user_id: username,
      message: values.content,
      conversation_id: actualSessionId,
      config_id: currentRagConfig === 'none' ? 'none' : (currentRagConfig || 'default')
    };
    
    try {
      let fullResponse = '';
      
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
          
          const botMessageId = maxMessageId + 2;
          const finalBotMessage = {
            content: fullResponse,
            messageId: botMessageId,
            modelId: 0,
            sessionId: actualSessionId,
            timestamp: new Date().toISOString(),
            userId: userId,
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
                userId: userId,
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
      const newSessionId = Cookies.get(5);
      if (newSessionId !== sessionId) {
        setSessionId(newSessionId);
      }
    };
    const interval = setInterval(handleSessionChange, 1000);
    return () => clearInterval(interval);
  }, [sessionId]);
  
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
          
          setMessages(formattedMessages);
        })
        .catch(error => {
          setMessages([]);
        });
    } else if (sessionId === DEFAULT_SESSION) {
      setMessages([]);
    } else {
      setMessages([]);
    }
  }, [sessionId]);
  
  useEffect(() => {
    fetchRagConfigurations();
  }, []); // 只在组件挂载时执行一次
  
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