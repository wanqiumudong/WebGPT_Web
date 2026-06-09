import './index.css';
import React, { useEffect, useMemo, useCallback, useState, useRef } from 'react';
import { Button, Form, Input, message, Upload } from 'antd';
import Cookies from 'js-cookie';
import { uploadLithoFile, fetchLithoStreaming } from '../../api/lithoApi';
import { MESSAGE_TYPE } from '../../constants';
import ChatMessage from '../../components/chatMessage';
import { CloudUploadOutlined, LoadingOutlined, DownloadOutlined } from '@ant-design/icons';
// 导入默认会话相关函数
import { DEFAULT_SESSION, createRealSessionAfterChat } from '../../components/history/history';
import { BACKEND_BASE_URL, CIRCUIT_BASE_URL, LITHO_BASE_URL } from '../../config/endpoints';

const normalizeLithoAssetUrl = (assetUrl) => {
  if (!assetUrl || typeof assetUrl !== 'string') {
    return assetUrl;
  }

  return assetUrl
    .replace(/https?:\/\/[^/]+\/static\/output\//g, `${LITHO_BASE_URL}/static/output/`)
    .replace(/https?:\/\/[^/]+\/static\/upload\//g, `${LITHO_BASE_URL}/static/upload/`);
};

const normalizeLithoMessageContent = (content) => {
  if (typeof content !== 'string') {
    return content;
  }

  return normalizeLithoAssetUrl(content);
};

const normalizeLithoMessage = (message) => {
  if (!message) {
    return message;
  }

  return {
    ...message,
    content: normalizeLithoMessageContent(message.content)
  };
};

const Chatbot = () => {
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [messages, setMessages] = useState([]);
  const username = Cookies.get('user');
  const userId = Cookies.get('userid') || Cookies.get('userId'); // 添加回退，支持两种 Cookie
  const [fileList, setFileList] = useState([]);
  const [sessionId, setSessionId] = useState(Cookies.get(2));
  const [streamConnection, setStreamConnection] = useState(null);
  const apiBaseUrl = BACKEND_BASE_URL;
  const titleBaseUrl = CIRCUIT_BASE_URL;
  
  // 使用 ref 跟踪流式消息索引
  const streamingBotMsgRef = useRef(null);
  
  useEffect(() => {
    const handleSessionChange = () => {
      const newSessionId = Cookies.get(2);
      if (newSessionId !== sessionId) {
        setSessionId(newSessionId);
      }
    };

    const interval = setInterval(handleSessionChange, 1000);
    return () => clearInterval(interval);
  }, [sessionId]);

  useEffect(() => {
    if (sessionId && sessionId !== DEFAULT_SESSION) {
      fetch(`${apiBaseUrl}/message/list-by-session?sessionId=${sessionId}`)
        .then(response => response.json())
        .then(data => {
          const normalizedMessages = Array.isArray(data)
            ? data.map(normalizeLithoMessage)
            : [];
          setMessages(normalizedMessages);
        })
        .catch(error => {
          console.error("Error fetching messages by session:", error);
          setMessages([]);
        });
    } else if (sessionId === DEFAULT_SESSION) {
      console.log('当前为默认会话状态，清空消息列表');
      setMessages([]);
    } else {
      setMessages([]);
    }
  }, [sessionId]);

  // 处理流式消息
  const handleStreamingMessage = async (values) => {
    try {
      let actualSessionId = sessionId;
      let isInDefaultSession = sessionId === DEFAULT_SESSION;
      
      // 如果在默认会话状态，先创建真实会话
      if (isInDefaultSession) {
        console.log('默认会话状态，创建真实会话');
        const newSessionId = await createRealSessionAfterChat(2); // modelId = 2 for 光刻
        if (newSessionId) {
          actualSessionId = newSessionId;
          setSessionId(newSessionId); // 更新当前组件的 sessionId
        } else {
          setLoading(false);
          message.error('创建会话失败');
          return;
        }
      }
      
      // 获取消息 ID
      const messageListResponse = await fetch(`${apiBaseUrl}/message/list-all`);
      const allMessages = await messageListResponse.json();
      const maxMessageId = allMessages.length ? Math.max(...allMessages.map(msg => msg.messageId)) : 0;
      
      // 创建用户消息
      const userMessage = {
        content: values.content,
        messageId: maxMessageId + 1,
        modelId: 2,
        sessionId: actualSessionId,
        timestamp: new Date().toISOString(),
        userId: userId,
        userType: MESSAGE_TYPE.USER,
      };
      
      // 保存用户消息到数据库
      await fetch(`${apiBaseUrl}/message/add`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(userMessage),
      });
      
      // 创建机器人响应占位符，添加 streaming 标志
      const botMessage = {
        content: "",
        messageId: maxMessageId + 2,
        modelId: 2,
        sessionId: actualSessionId,
        timestamp: new Date().toISOString(),
        userId: userId,
        userType: MESSAGE_TYPE.BOT,
        streaming: true,
      };
      
      // 更新 UI
      setMessages(prev => [...prev, userMessage, botMessage]);
      
      // 清空表单
      await form.resetFields();
      
      // 记录流式消息索引
      const botMessageIndex = messages.length + 1;
      streamingBotMsgRef.current = botMessageIndex;
      
      let accumulatedContent = "";
      
      // 连接流式 API
      const params = { user_id: username, message: values.content };
      const connection = fetchLithoStreaming(
        params,
        // 数据回调
        (data) => {
          if (data && data.chunk !== undefined) {
            accumulatedContent += data.chunk;
            const normalizedContent = normalizeLithoMessageContent(accumulatedContent);
            
            setMessages(prev => {
              const updated = [...prev];
              if (updated[botMessageIndex]) {
                updated[botMessageIndex] = {
                  ...updated[botMessageIndex],
                  content: normalizedContent,
                };
              }
              return updated;
            });
          }
        },
        // 完成回调
        async () => {
          try {
            setLoading(false);
            
            setMessages(prev => {
              const updated = [...prev];
              if (updated[botMessageIndex]) {
                updated[botMessageIndex] = {
                  ...updated[botMessageIndex],
                  streaming: false,
                };
              }
              return updated;
            });
            
            // 保存完整 Bot 消息
            const completeBotMessage = {
              content: normalizeLithoMessageContent(accumulatedContent),
              messageId: maxMessageId + 2,
              modelId: 2,
              sessionId: actualSessionId,
              timestamp: new Date().toISOString(),
              userId: userId,
              userType: MESSAGE_TYPE.BOT,
            };
            
            await fetch(`${apiBaseUrl}/message/add`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify(completeBotMessage),
            });

            // 生成并更新会话标题
            try {
              const sessionResponse = await fetch(`${apiBaseUrl}/session/get?sessionId=${actualSessionId}`);
              if (sessionResponse.ok) {
                const currentSession = await sessionResponse.json();
                if (currentSession && currentSession.header && currentSession.header !== '新会话') {
                  return;
                }
              }
              
              const response = await fetch(`${apiBaseUrl}/message/list-by-session?sessionId=${actualSessionId}`);
              const messagesList = await response.json();
              
              const firstUserMessage = messagesList.find(m => m.userType === MESSAGE_TYPE.USER);
              const firstBotMessage = messagesList.find(m => m.userType === MESSAGE_TYPE.BOT);
              
              if (firstUserMessage) {
                let generatedTitle = '';
                
                try {
                  if (firstBotMessage && accumulatedContent) {
                    const titleResponse = await fetch(`${titleBaseUrl}/generate_session_title`, {
                      method: 'POST',
                      headers: { 'Content-Type': 'application/json' },
                      body: JSON.stringify({
                        user_message: firstUserMessage.content,
                        bot_response: accumulatedContent,
                        message_type: 'lithography'
                      })
                    });
                    
                    if (titleResponse.ok) {
                      const titleData = await titleResponse.json();
                      if (titleData.title) {
                        generatedTitle = titleData.title;
                      }
                    }
                  }
                } catch (titleError) {}
                
                if (!generatedTitle) {
                  const userContent = firstUserMessage.content || '';
                  if (userContent.includes('<img') || userContent.includes('src=') || userContent.includes('文件上传')) {
                    generatedTitle = '图片分析';
                  } else {
                    const cleanContent = userContent.replace(/<[^>]*>/g, '');
                    generatedTitle = cleanContent.slice(0, 8) || '新会话';
                  }
                }
                
                const headerUpdate = {
                  createTime: new Date().toISOString(),
                  header: generatedTitle || '新会话',
                  lastActive: new Date().toISOString(),
                  modelId: 2,
                  sessionId: actualSessionId,
                  status: 1,
                  userId: userId,
                };
          
                await fetch(`${apiBaseUrl}/session/update`, {
                  method: "POST",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify(headerUpdate),
                });
              }
            } catch (error) {
              console.error("更新会话错误:", error);
            }
          } finally {
            streamingBotMsgRef.current = null;
            setStreamConnection(null);
          }
        },
        // 错误回调
        (error) => {
          console.error("流式 API 错误:", error);
          setLoading(false);
          
          setMessages(prev => {
            const updated = [...prev];
            if (updated[botMessageIndex]) {
              updated[botMessageIndex] = {
                ...updated[botMessageIndex],
                content: "抱歉，出现了错误，请稍后再试。",
                streaming: false,
              };
            }
            return updated;
          });
          
          streamingBotMsgRef.current = null;
          setStreamConnection(null);
        }
      );
      
      setStreamConnection(connection);
      
    } catch (error) {
      console.error("处理流式消息错误:", error);
      setLoading(false);
      message.error("请求出错，请稍后再试");
    }
  };

  // 修改提交处理函数，使用流式 API
  const onhandleFinished = async () => {
    const values = await form.getFieldsValue();
    if (!values?.content) return;

    if (streamingBotMsgRef.current !== null) {
      message.info("正在处理上一条消息，请稍等");
      return;
    }

    setLoading(true);
    await handleStreamingMessage(values);
  };

  const filterBotMessages = (submitMessages) => {
    return submitMessages.filter((item) => !item.loading) || [];
  };

  useEffect(() => {
    const initMessages = localStorage.getItem('guangkeBot');
    if (!!initMessages) {
      try {
        const parsedMessages = JSON.parse(initMessages);
        setMessages(Array.isArray(parsedMessages) ? parsedMessages.map(normalizeLithoMessage) : []);
      } catch (e) {}
    }
  }, []);

  useEffect(() => {
    const container = document.querySelector('.chat-message-list');
    if (container) {
      setTimeout(() => {
        container.scrollTop = container.scrollHeight;
      }, 100);
    }
  }, [messages]);

  // 组件卸载时清理流连接
  useEffect(() => {
    return () => {
      if (streamConnection) {
        streamConnection.cancel();
      }
    };
  }, [streamConnection]);

  const beforeUpload = (file) => {
    setFileList([]);
    if (file.size / 1024 / 1024 > 5) {
      message.error("文件大小限制在 5MB 以内");
      return Upload.LIST_IGNORE;
    }
    return true;
  };

  const onUploadFile = useCallback(
    async (file, onSuccess, onError) => {
      try {
        let currentSessionId = Cookies.get('guangke_sessionId') || sessionId;

        if (currentSessionId && currentSessionId !== sessionId) {
          setSessionId(currentSessionId);
        }

        var formData = new FormData();
        formData.append('file', file);
        formData.append('type', file.type);

        const messageListResponse = await fetch(`${apiBaseUrl}/message/list-all`);
        const allMessages = await messageListResponse.json();
        const maxMessageId = allMessages.length ? Math.max(...allMessages.map(msg => msg.messageId)) : 0;

        const newMessage = {
          content: `${file.name} 文件上传`,
          messageId: maxMessageId + 1,
          modelId: 3,
          sessionId: currentSessionId,
          timestamp: new Date().toISOString(),
          userId: userId,
          userType: MESSAGE_TYPE.USER,
        };

        await fetch(`${apiBaseUrl}/message/add`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(newMessage),
        });

        uploadLithoFile(formData, {
          headers: {
            'Content-Type': 'multipart/form-data',
          },
        })
          .then((response) => {
            if (response?.status === 200) {
              onSuccess(response);

              const fileUrl = URL.createObjectURL(file);
              const uploadMessage = {
                content: `${file.name} 文件上传成功`,
                userType: MESSAGE_TYPE.BOT,
                fileUrl: fileUrl,
                fileName: file.name
              };

              setMessages((prevMessages) => [...prevMessages, uploadMessage]);

              const newMessage = {
                content: `${file.name} 文件上传成功`,
                messageId: maxMessageId + 2,
                modelId: 2,
                sessionId: currentSessionId,
                timestamp: new Date().toISOString(),
                userId: userId,
                userType: MESSAGE_TYPE.BOT,
              };
        
              fetch(`${apiBaseUrl}/message/add`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(newMessage),
              });

            } else {
              onError(response);
              const uploadMessage = {
                content: `${file.name} 文件上传失败`,
                userType: MESSAGE_TYPE.BOT,
              };

              setMessages((prevMessages) => [...prevMessages, uploadMessage]);

              const newMessage = {
                content: `${file.name} 文件上传失败`,
                messageId: maxMessageId + 2,
                modelId: 2,
                sessionId: currentSessionId,
                timestamp: new Date().toISOString(),
                userId: userId,
                userType: MESSAGE_TYPE.BOT,
              };
        
              fetch(`${apiBaseUrl}/message/add`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(newMessage),
              });
            }
          })
          .catch(function (error) {
            console.error('Upload error:', error);
          });
      } catch (error) {
        console.error('光刻文件上传出错:', error);
        onError(error);
      }
    },
    [sessionId],
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

          const isImage = file.type.startsWith('image/');
          if (isImage) {
            const fileToUse = file.originFileObj || file;
            const submittedImageUrl = URL.createObjectURL(fileToUse);

            const newUploadMessage = {
              content: <img className="submitted-image" src={submittedImageUrl} alt="加载失败..." style={{ maxWidth: '500px', maxHeight: '500px' }}  />,
              userType: MESSAGE_TYPE.USER,
              type: 'img'
            };

            messageList.push(newUploadMessage);
          } else {
            messageList.push({
              content: `${fileNames.join(', ')} 文件上传`,
              userType: MESSAGE_TYPE.USER,
            });
          }

          setMessages(messageList);
        }

        if (status === 'done') {
          setUploading(false);
        } else if (status === 'error') {
          setUploading(false);
          message.error(`${info.file.name} 上传失败`);
        }
      },
    };
  }, [messages]);

  return (
    <div className='guangke'>
      {!messages.length && (
        <div className='guangke-empty'>
          <div className='guangke-title'>您好，我是光刻大模型</div>
          <div className='guangke-question'>有什么相关问题吗？</div>
          <div className='guangke-intro'>支持逆向光刻技术的模拟、优化和评估</div>
          <div className='guangke-intro' style={{ marginTop: -10 }}>支持 SimpleILT、LevelSet、Neural-ILT 等模型</div>
          <div className='guangke-intro' style={{ marginTop: -10 }}>请上传您要优化的掩模图标或优化好的掩模并输入指令</div>
        </div>
      )}
      {messages.length > 0 && (
        <div className="chat-message-list">
          {messages.map((item, index) => ( 
            <div key={index} className="chat-message-item" style={{ display: 'flex', alignItems: 'center' }}>
              <ChatMessage
                sendType={item.userType}
                message={item.content}
                loading={item.loading}
                streaming={item.streaming}
                type={item.type}
              />
              {item.fileUrl && (
                <a
                  href={item.fileUrl}
                  download={item.fileName}
                  style={{
                    transform: 'translateX(-80px)',
                    marginLeft: '-20px',
                    color: 'blue',
                    textDecoration: 'underline',
                    cursor: 'pointer'
                  }}
                >
                  <DownloadOutlined style={{ fontSize: '18px', color: '#1890ff' }} />
                </a>
              )}
            </div>
          ))}
        </div>
      )}
      <div className='guangke-footer'>
        <Form
          form={form}
          layout='inline'
          style={{ width: '100%' }}
          onFinish={onhandleFinished}
          autoComplete='off'
        >
          <Form.Item name='content' style={{ width: 'calc(100% - 150px)' }}>
            <Input placeholder='尽管提问...'></Input>
          </Form.Item>
          <Upload {...uploadProps} fileList={fileList}>
            <Button>
              {uploading ? <LoadingOutlined /> : <CloudUploadOutlined />}</Button>
          </Upload>

          <div className='devide-line'></div>
          <Form.Item>
            <Button
              disabled={loading}
              loading={loading}
              htmlType='submit'
              icon={
                <img
                  src={require('../../assets/send.png')}
                  style={{ height: 32, width: 32 }}
                ></img>
              }
            ></Button>
          </Form.Item>
        </Form>
      </div>
    </div>
  );
};

export default Chatbot;
