import './index.css';
import React, { useEffect, useMemo, useCallback, useState, useRef } from 'react';
import { Button, Form, Input, message, Upload } from 'antd';
import Cookies from 'js-cookie';
import axios from 'axios';
import { fetchCircuitStreaming, uploadCircuitImage } from '../../api/circuitApi';
import { MESSAGE_TYPE, THINKING_TEXTS } from '../../constants';
import ChatMessage from '../../components/chatMessage';
import { LoadingOutlined, CloudUploadOutlined, StopOutlined } from '@ant-design/icons';
// 导入默认会话相关函数
import { DEFAULT_SESSION, createRealSessionAfterChat, isDefaultSession } from '../../components/history/history';

// =============== 常量配置 ===============
const MODEL_ID = 5;
const COOKIE_KEY = 'circuit_5';
const BACKEND_API_BASE = "http://10.98.64.22:8080";

// =============== 工具函数 ===============
const preprocessModelOutput = (content) => {
  if (!content) return content;
  
  return content
    .replace(/<think>/g, '**首先，让我先分析一下整个电路**\n```\n')
    .replace(/<\/think>/g, '\n```\n')
    .replace(/<answer>/g, '**最终的网表为：**\n```\n')
    .replace(/<\/answer>/g, '\n```\n')
    .replace(/<Port>/g, '**端口识别：**\n')
    .replace(/<\/Port>/g, '\n')
    .replace(/<Device>/g, '**器件识别：**\n')
    .replace(/<\/Device>/g, '\n')
    .replace(/<Connection>/g, '**连接关系：**\n')
    .replace(/<\/Connection>/g, '\n');
};

const processMessage = (msg) => {
  // 🔧 修复：数据库返回的userType格式不一致
  // 统一转换userType格式: "0"/"bot" -> MESSAGE_TYPE.BOT, "user" -> MESSAGE_TYPE.USER
  const normalizedMsg = {
    ...msg,
    userType: msg.userType === "0" || msg.userType === "bot" ? MESSAGE_TYPE.BOT : MESSAGE_TYPE.USER
  };
  
  if (normalizedMsg.userType === MESSAGE_TYPE.BOT && normalizedMsg.content) {
    if (normalizedMsg.content.includes('<img')) {
      return normalizedMsg;
    }
    return { ...normalizedMsg, content: preprocessModelOutput(normalizedMsg.content) };
  }
  return normalizedMsg;
};

// =============== API函数 ===============
const apiRequest = async (endpoint, options = {}) => {
  const url = `${BACKEND_API_BASE}/${endpoint}`;
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
    
    // 检查响应是否有内容
    const text = await response.text();
    
    if (!text || text.trim() === '') {
      // 对于session/update，空响应通常意味着成功
      if (endpoint === 'session/update') {
        return { success: true, message: 'Session updated successfully' };
      }
      return null;
    }
    
    // 尝试解析JSON
    try {
      return JSON.parse(text);
    } catch (jsonError) {
      // 对于session/update，非JSON响应也可能是成功的
      if (endpoint === 'session/update') {
        return { success: true, message: text };
      }
      return text; // 返回原始文本
    }
  } catch (error) {
    throw error;
  }
};

const getMaxMessageId = async () => {
  try {
    const allMessages = await apiRequest("message/list-all");
    return allMessages.length ? Math.max(...allMessages.map(msg => msg.messageId || msg.message_id || 0)) : 0;
  } catch (error) {
    return Date.now(); // 使用时间戳作为备选
  }
};

const createSession = async (userId, header = "CircuitThink会话") => {
  const sessionData = {
    createTime: new Date().toISOString(),
    header,
    lastActive: new Date().toISOString(),
    modelId: MODEL_ID,
    status: 1,
    userId: parseInt(userId)
  };
  
  const result = await apiRequest("session/add", {
    method: "POST",
    body: JSON.stringify(sessionData)
  });
  
  return result.sessionId;
};

const addMessage = async (messageData) => {
  try {
    // 确保消息包含所有必需字段 - 如果userId无效就跳过保存
    if (!messageData.sessionId) {
      return { success: false, error: 'sessionId为空' };
    }
    
    // 对于userId，如果是undefined或无效，就使用1作为默认值（admin用户）
    let validUserId = 1; // 默认使用admin用户ID
    if (messageData.userId && !isNaN(parseInt(messageData.userId))) {
      validUserId = parseInt(messageData.userId);
    }
    
    const completeMessage = {
      content: messageData.content || '',
      messageId: messageData.messageId,
      modelId: MODEL_ID,
      sessionId: messageData.sessionId,
      timestamp: messageData.timestamp || new Date().toISOString(),
      userId: validUserId, // 确保始终是有效的数字
      userType: messageData.userType,
    };
    
    
    const response = await fetch(`${BACKEND_API_BASE}/message/add`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(completeMessage),
    });
    
    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(`HTTP error! status: ${response.status}, body: ${errorText}`);
    }
    
    const result = await response.json();
    return result;
  } catch (error) {
    // 为了让前端流程继续，不抛出错误，但记录详细错误信息
    return { success: false, error: error.message };
  }
};

const updateSessionHeader = async (sessionId, userId, userMessage = '', botMessage = '', apiPort) => {
  try {
    
    // 🔧 首先检查当前会话是否已经有自定义标题
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
      // 使用传入的消息或从数据库获取的消息
      const userContent = userMessage || firstUserMessage.content;
      const botContent = botMessage || (messagesList.find(m => m.userType === MESSAGE_TYPE.BOT)?.content || '');
      
      let generatedTitle = '';
      
      // 直接使用简单的标题生成策略，不调用API
      
      // 使用简单的标题生成策略
      // 检查是否是图片上传场景
      if (userContent.includes('<img') || userContent.includes('src=') || userContent.includes('请分析这张电路图')) {
        generatedTitle = '电路图理解';
      } else {
        // 使用用户输入的前8个字符，过滤HTML标签
        const cleanContent = userContent.replace(/<[^>]*>/g, '');
        generatedTitle = cleanContent.slice(0, 8) || '新对话';
      }

      const headerUpdate = {
        createTime: new Date().toISOString(),
        header: generatedTitle,
        lastActive: new Date().toISOString(),
        modelId: MODEL_ID,
        sessionId: sessionId,
        status: 1,
        userId: parseInt(userId),
      };

      
      const result = await apiRequest("session/update", {
        method: "POST",
        body: JSON.stringify(headerUpdate)
      });
      
      
      // 确保触发历史更新事件
      window.sessionUpdated = Date.now();
      window.dispatchEvent(new Event("sessionUpdated"));
      
      // 额外的历史刷新触发（备用）
      setTimeout(() => {
        window.dispatchEvent(new Event("sessionUpdated"));
      }, 500);
    }
  } catch (error) {
    // 不抛出错误，让主流程继续
  }
};

// =============== 主组件 ===============
const CircuitThink = ({ port }) => {
  // =============== 状态管理 ===============
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(false);
  const [messages, setMessages] = useState([]);
  const [uploading, setUploading] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const [fileList, setFileList] = useState([]);
  const [sessionId, setSessionId] = useState(Cookies.get(COOKIE_KEY));
  const [analyzing, setAnalyzing] = useState(false); // 添加分析状态
  

  // 添加动态思考效果相关状态
  const [dynamicThinkingText, setDynamicThinkingText] = useState('思考中...');
  const [dynamicAnalyzingText, setDynamicAnalyzingText] = useState('电路解析中...');
  const thinkingIntervalRef = useRef(null);
  const analyzingIntervalRef = useRef(null);
  
  const username = Cookies.get('user');
  const userId = Cookies.get('userid') || Cookies.get('userId'); // 添加回退，支持两种Cookie键
  const streamRequestRef = useRef(null);
  const isUserAbortingRef = useRef(false); // 添加用户中断标志

  // =============== 状态管理辅助函数 ===============
  const setLoadingStates = useCallback((isLoading, isStreaming = false) => {
    setLoading(isLoading);
    setStreaming(isStreaming);
  }, []);

  // 动态思考效果函数
  const startThinkingAnimation = useCallback(() => {
    if (thinkingIntervalRef.current) return; // 防止重复启动
    
    let textIndex = 0;
    thinkingIntervalRef.current = setInterval(() => {
      setDynamicThinkingText(THINKING_TEXTS[textIndex]);
      textIndex = (textIndex + 1) % THINKING_TEXTS.length;
    }, 500); // 每500ms切换一次
  }, []);
  
  const stopThinkingAnimation = useCallback(() => {
    if (thinkingIntervalRef.current) {
      clearInterval(thinkingIntervalRef.current);
      thinkingIntervalRef.current = null;
    }
    setDynamicThinkingText('思考中...'); // 重置为默认文本
  }, []);
  
  // 动态电路解析效果函数
  const startAnalyzingAnimation = useCallback(() => {
    if (analyzingIntervalRef.current) return; // 防止重复启动
    
    const analyzingTexts = [
      '电路解析中.',
      '电路解析中..',
      '电路解析中...',
      '正在分析电路.',
      '正在分析电路..',
      '正在分析电路...'
    ];
    
    let textIndex = 0;
    analyzingIntervalRef.current = setInterval(() => {
      setDynamicAnalyzingText(analyzingTexts[textIndex]);
      textIndex = (textIndex + 1) % analyzingTexts.length;
    }, 500); // 每500ms切换一次
  }, []);
  
  const stopAnalyzingAnimation = useCallback(() => {
    if (analyzingIntervalRef.current) {
      clearInterval(analyzingIntervalRef.current);
      analyzingIntervalRef.current = null;
    }
    setDynamicAnalyzingText('电路解析中...'); // 重置为默认文本
  }, []);

  const resetStates = useCallback(() => {
    setLoadingStates(false, false);
    setAnalyzing(false); // 重置分析状态
    streamRequestRef.current = null;
    isUserAbortingRef.current = false; // 重置中断标志
    // 停止所有动画
    stopThinkingAnimation();
    stopAnalyzingAnimation();
  }, [setLoadingStates, stopThinkingAnimation, stopAnalyzingAnimation]);

  // =============== 会话管理 ===============
  useEffect(() => {
    const handleSessionChange = () => {
      const newSessionId = Cookies.get(COOKIE_KEY);
      if (newSessionId !== sessionId) {
        setSessionId(newSessionId);
      }
    };

    // 🔧 监听会话删除事件
    const handleSessionDeleted = (event) => {
      const { deletedSessionId, modelId, shouldResetToDefault } = event.detail;
      
      if (modelId === MODEL_ID && shouldResetToDefault) {
        // 强制重置所有状态
        setMessages([]);
        setLoadingStates(false, false);
        setAnalyzing(false);
        setFileList([]);
        
        // 清除流式请求
        if (streamRequestRef.current) {
          try {
            streamRequestRef.current.abort();
          } catch (e) {
          }
          streamRequestRef.current = null;
        }
        
        // 强制设置为DEFAULT_SESSION并立即触发状态更新
        setSessionId(DEFAULT_SESSION);
        
        // 延迟检查并更新sessionId，确保Cookie已经更新
        setTimeout(() => {
          const newSessionId = Cookies.get(COOKIE_KEY);
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
  }, [sessionId, setLoadingStates]);

  // =============== 消息加载 ===============
  useEffect(() => {
    if (sessionId && sessionId !== DEFAULT_SESSION) {
      apiRequest(`message/list-by-session?sessionId=${sessionId}`)
        .then(data => {
          
          if (!Array.isArray(data)) {
            setMessages([]);
            return;
          }
          
          if (data.length === 0) {
            setMessages([]);
            return;
          }
          
          // 排序消息确保正确的时间顺序
          const sortedData = data.sort((a, b) => {
            const timeA = new Date(a.timestamp).getTime();
            const timeB = new Date(b.timestamp).getTime();
            return timeA - timeB;
          });
          
          const processedMessages = sortedData.map(processMessage);
          setMessages(processedMessages);
        })
        .catch(error => {
          
          // 检查是否是会话不存在的错误
          if (error.message && error.message.includes('Session not exists')) {
            // 清除无效的会话ID Cookie
            Cookies.remove(COOKIE_KEY);
            // 设置为默认会话状态
            Cookies.set(COOKIE_KEY, DEFAULT_SESSION, { expires: 7 });
            setSessionId(DEFAULT_SESSION);
          }
          
          setMessages([]);
        });
    } else if (sessionId === DEFAULT_SESSION) {
      setMessages([]);
    } else {
      setMessages([]);
    }
  }, [sessionId]);

  // =============== 图片分析专用函数 ===============
  const startImageAnalysis = async (userMessageId = null) => {
    // 防重复请求保护
    if (analyzing || streaming) {
      return;
    }
    
    setAnalyzing(true);
    setLoadingStates(true, true);
    
    try {
      // 🔧 强制重新读取最新的会话ID，避免使用过期状态
      let actualSessionId = Cookies.get(COOKIE_KEY);

      // 如果状态和cookie不一致，更新状态
      if (actualSessionId && actualSessionId !== sessionId) {
        setSessionId(actualSessionId);
      }

      let isInDefaultSession = actualSessionId === DEFAULT_SESSION;
      
      // 如果是默认会话状态，先创建真实会话
      if (isInDefaultSession) {
        const newSessionId = await createRealSessionAfterChat(MODEL_ID);
        if (newSessionId) {
          actualSessionId = newSessionId;
          setSessionId(newSessionId);
          Cookies.set(COOKIE_KEY, newSessionId, { expires: 7 });
        } else {
          setAnalyzing(false);
          setLoadingStates(false, false);
          message.error('创建会话失败');
          return;
        }
      }
      
      // 如果传入了用户消息ID，使用它来计算bot消息ID，否则重新获取
      let maxMessageId;
      if (userMessageId) {
        maxMessageId = userMessageId; // 用户消息已经是maxMessageId + 1，所以bot消息应该是maxMessageId + 1
      } else {
        maxMessageId = await getMaxMessageId();
      }
      
      // 只添加机器人消息，显示正在分析
      const botMessage = {
        content: dynamicAnalyzingText,
        messageId: maxMessageId + 1,
        sessionId: actualSessionId,
        userId: userId,
        userType: MESSAGE_TYPE.BOT,
        streaming: true,
        isLoading: true  // 关键：添加加载状态标志
      };
      
      setMessages(prev => [...prev, botMessage]);
      
      // 启动动态分析效果
      startAnalyzingAnimation();
      
      // 等待状态更新后再开始流式请求 - 修复语法错误：添加async
      setTimeout(async () => {
        let accumulatedContent = "";
        
        // 发送分析请求，但不显示用户的提问文本
        const params = { 
          user_id: username, 
          message: "请分析这张电路图", // 这个只发给后端，不显示给用户
          conversation_id: actualSessionId,
          model_id: MODEL_ID
        };
        
        const connection = await fetchCircuitStreaming(
          params,
          (chunk) => {  // 🔧 修复：与普通对话一致，直接使用chunk参数
            if (chunk) {
              accumulatedContent += chunk;
              
              setMessages(prevMessages => {
                const updated = [...prevMessages];
                const lastIndex = updated.length - 1;
                
                if (updated[lastIndex] && updated[lastIndex].userType === MESSAGE_TYPE.BOT) {
                  if (updated[lastIndex].isLoading) {
                    // 还在加载状态：只有当内容足够长时才转换
                    if (accumulatedContent.trim().length > 10) {
                      const isHtml = accumulatedContent.includes('<img');
                      updated[lastIndex] = {
                        ...updated[lastIndex],
                        content: isHtml ? accumulatedContent : preprocessModelOutput(accumulatedContent),
                        isLoading: false  // 关闭加载状态
                      };
                    }
                    // 如果内容太短，保持"电路解析中..."
                  } else {
                    // 正常流式更新
                    const isHtml = accumulatedContent.includes('<img');
                    updated[lastIndex] = {
                      ...updated[lastIndex],
                      content: isHtml ? accumulatedContent : preprocessModelOutput(accumulatedContent)
                    };
                  }
                }
                return updated;
              });
            }
          },
          async () => {
            resetStates();
            setAnalyzing(false);
            
            setMessages(prevMessages => {
              const updated = [...prevMessages];
              const lastIndex = updated.length - 1;
              if (updated[lastIndex] && updated[lastIndex].userType === MESSAGE_TYPE.BOT) {
                updated[lastIndex] = {
                  ...updated[lastIndex],
                  streaming: false,
                  isLoading: false  // 确保关闭加载状态
                };
              }
              return updated;
            });
            
            const completeBotMessage = {
              content: accumulatedContent,
              messageId: maxMessageId + 1,
              sessionId: actualSessionId,
              userId: userId,
              userType: MESSAGE_TYPE.BOT,
            };
            
            try {
              await addMessage(completeBotMessage);
              await updateSessionHeader(actualSessionId, userId, "请分析这张电路图", accumulatedContent, port);
            } catch (error) {
            }
          },
          (error) => {
            
            // 如果是用户主动中断，不处理错误
            if (isUserAbortingRef.current) {
              return;
            }
            
            resetStates();
            setAnalyzing(false);
            
            // 检查是否是用户中断错误
            let errorMessage = "抱歉，电路图分析出现了错误，请稍后再试。";
            if (error && (error.name === 'AbortError' || (error.message && (error.message === 'BodyStreamBuffer was aborted' || error.message.includes('aborted'))))) {
              errorMessage = "图片分析已中断";
            } else if (error && error.message && (
              error.message.includes("longer than the maximum model length") ||
              error.message.includes("Make sure that max_model_len is no smaller") ||
              error.message.includes("The decoder prompt") ||
              error.message.includes("vLLM生成出错")
            )) {
              errorMessage = "文件过大，请尝试上传更小的图片文件。";
            }
            
            setMessages(prevMessages => {
              const updated = [...prevMessages];
              const lastIndex = updated.length - 1;
              if (updated[lastIndex] && updated[lastIndex].userType === MESSAGE_TYPE.BOT) {
                updated[lastIndex] = {
                  ...updated[lastIndex],
                  content: errorMessage,
                  streaming: false,
                  isLoading: false
                };
              }
              return updated;
            });
          }
        );
        
        streamRequestRef.current = connection;
      }, 100);
      
    } catch (error) {
      resetStates();
      setAnalyzing(false);
      message.error("启动电路分析失败，请稍后再试");
    }
  };

  // =============== 统一的流式对话处理函数 ===============
  const handleStreamingChat = async (userMessage, isImageUpload = false, skipUserMessage = false) => {
    setLoadingStates(true, true);
    
    try {
      // 🔧 强制重新读取最新的会话ID，避免使用过期状态
      let actualSessionId = Cookies.get(COOKIE_KEY);

      // 如果状态和cookie不一致，更新状态
      if (actualSessionId && actualSessionId !== sessionId) {
        setSessionId(actualSessionId);
      }

      let isInDefaultSession = actualSessionId === DEFAULT_SESSION;
      
      // 如果是默认会话状态，先创建真实会话
      if (isInDefaultSession) {
        const newSessionId = await createRealSessionAfterChat(MODEL_ID); // MODEL_ID = 5
        if (newSessionId) {
          actualSessionId = newSessionId;
          setSessionId(newSessionId); // 更新当前组件的sessionId
          Cookies.set(COOKIE_KEY, newSessionId, { expires: 7 }); // 更新cookie
        } else {
          setLoadingStates(false, false);
          message.error('创建会话失败');
          return;
        }
      }
      
      const maxMessageId = await getMaxMessageId();
      let currentBotMessageIndex;
      
      if (!skipUserMessage) {
        // 正常对话：先添加用户消息
        const userMessageData = {
          content: userMessage,
          messageId: maxMessageId + 1,
          sessionId: actualSessionId,
          userId: userId,
          userType: MESSAGE_TYPE.USER
        };
        
        await addMessage(userMessageData);
        
        const botMessage = {
          content: isImageUpload ? dynamicAnalyzingText : dynamicThinkingText,  // 根据类型显示不同提示
          messageId: maxMessageId + 2,
          sessionId: actualSessionId,
          userId: userId,
          userType: MESSAGE_TYPE.BOT,
          streaming: true,
          isLoading: true  // 添加加载状态标志
        };
        
        setMessages(prev => [...prev, userMessageData, botMessage]);
        currentBotMessageIndex = messages.length + 1;
        
        // 启动对应的动画
        if (isImageUpload) {
          startAnalyzingAnimation();
        } else {
          startThinkingAnimation();
        }
      } else {
        // 图片上传：只添加机器人消息
        const botMessage = {
          content: dynamicAnalyzingText,
          messageId: maxMessageId + 1,
          sessionId: actualSessionId,
          userId: userId,
          userType: MESSAGE_TYPE.BOT,
          streaming: true,
          isLoading: true  // 添加加载状态标志
        };
        
        setMessages(prev => [...prev, botMessage]);
        currentBotMessageIndex = messages.length;
        
        // 启动分析动画
        startAnalyzingAnimation();
      }
      
      let accumulatedContent = "";
      
      const params = { 
        user_id: username, 
        message: userMessage,
        conversation_id: actualSessionId,
        model_id: MODEL_ID
      };
      
      const connection = await fetchCircuitStreaming(
        params,
        (chunk) => {  // 🔧 修复：circuitApi.js直接传递chunk字符串，不是{chunk: "..."}对象
          if (chunk) {
            accumulatedContent += chunk;
            
            setMessages(prev => {
              const updated = [...prev];
              if (updated[currentBotMessageIndex]) {
                if (updated[currentBotMessageIndex].isLoading) {
                  // 还在加载状态：只有当内容足够长时才转换
                  if (accumulatedContent.trim().length > 10) {
                    const isHtml = accumulatedContent.includes('<img');
                    updated[currentBotMessageIndex] = {
                      ...updated[currentBotMessageIndex],
                      content: isHtml ? accumulatedContent : preprocessModelOutput(accumulatedContent),
                      isLoading: false  // 关闭加载状态
                    };
                  }
                } else {
                  // 正常流式更新
                  const isHtml = accumulatedContent.includes('<img');
                  updated[currentBotMessageIndex] = {
                    ...updated[currentBotMessageIndex],
                    content: isHtml ? accumulatedContent : preprocessModelOutput(accumulatedContent)
                  };
                }
              }
              return updated;
            });
          }
        },
        async () => {
          resetStates();
          
          setMessages(prev => {
            const updated = [...prev];
            if (updated[currentBotMessageIndex]) {
              updated[currentBotMessageIndex] = {
                ...updated[currentBotMessageIndex],
                streaming: false,
                isLoading: false  // 确保关闭加载状态
              };
            }
            return updated;
          });
          
          // ✅ 统一架构：前端负责保存助手回复，与TCAD/Chatbot保持一致
          const completeBotMessage = {
            content: accumulatedContent,
            messageId: skipUserMessage ? maxMessageId + 1 : maxMessageId + 2,
            sessionId: actualSessionId,
            userId: userId,
            userType: MESSAGE_TYPE.BOT,
          };
          
          try {
            await addMessage(completeBotMessage);
            await updateSessionHeader(actualSessionId, userId, userMessage, accumulatedContent, port);
          } catch (error) {
          }
        },
        (error) => {
          
          // 如果是用户主动中断，不处理错误
          if (isUserAbortingRef.current) {
            return;
          }
          
          resetStates();
          
          // 检查是否是用户中断错误
          let errorMessage = "抱歉，出现了错误，请稍后再试。";
          if (error && (error.name === 'AbortError' || (error.message && (error.message === 'BodyStreamBuffer was aborted' || error.message.includes('aborted'))))) {
            errorMessage = "对话已中断";
          } else if (error && error.message && (
            error.message.includes("longer than the maximum model length") ||
            error.message.includes("Make sure that max_model_len is no smaller") ||
            error.message.includes("The decoder prompt") ||
            error.message.includes("vLLM生成出错")
          )) {
            errorMessage = "文件过大，请尝试上传更小的图片文件。";
          }
          
          setMessages(prev => {
            const updated = [...prev];
            if (updated[currentBotMessageIndex]) {
              updated[currentBotMessageIndex] = {
                ...updated[currentBotMessageIndex],
                content: errorMessage,
                streaming: false,
                isLoading: false
              };
            }
            return updated;
          });
        }
      );
      
      streamRequestRef.current = connection;
      
    } catch (error) {
      resetStates();
      message.error("请求出错，请稍后再试");
    }
  };

  // =============== 消息发送处理 ===============
  const onhandleFinished = async () => {
    const values = await form.getFieldsValue();
    
    if (!values?.content) return;
    
    await form.resetFields();
    await handleStreamingChat(values.content, false);
  };

  // =============== 文件上传处理 ===============
  const beforeUpload = (file) => {
    setFileList([]);
    
    // 检查文件类型 - 只允许PNG、JPG、JPEG
    const fileType = file.type || '';
    const fileName = file.name || '';
    const fileExtension = fileName.toLowerCase().split('.').pop();
    
    const supportedTypes = ['image/png', 'image/jpg', 'image/jpeg'];
    const supportedExtensions = ['png', 'jpg', 'jpeg'];
    
    if (!supportedTypes.includes(fileType) && !supportedExtensions.includes(fileExtension)) {
      message.error("只支持PNG、JPG、JPEG格式的图片文件");
      return Upload.LIST_IGNORE;
    }
    
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
        let currentSessionId = Cookies.get(COOKIE_KEY);

        // 如果状态和cookie不一致，更新状态
        if (currentSessionId && currentSessionId !== sessionId) {
          setSessionId(currentSessionId);
        }

        // 如果是默认会话状态，先创建真实会话
        if (currentSessionId === DEFAULT_SESSION) {
          const newSessionId = await createRealSessionAfterChat(MODEL_ID);
          if (newSessionId) {
            currentSessionId = newSessionId;
            setSessionId(newSessionId);
            Cookies.set(COOKIE_KEY, newSessionId, { expires: 7 });
          } else {
            message.error('创建会话失败');
            onError(new Error('创建会话失败'));
            return;
          }
        }

        // 确保会话存在
        if (!currentSessionId) {
          currentSessionId = await createSession(userId, "CircuitThink文件上传会话");
          setSessionId(currentSessionId);
          Cookies.set(COOKIE_KEY, currentSessionId);
        }

        const maxMessageId = await getMaxMessageId();

        // 上传文件
        var formData = new FormData();
        formData.append('file', file);
        formData.append('type', file.type);
        formData.append('conversation_id', currentSessionId);
        formData.append('user_id', username);

        const uploadKey = 'uploadFile';
        message.loading({ content: '正在上传文件...', key: uploadKey });

        uploadCircuitImage(file, currentSessionId, username)
          .then(async (response) => {
            
            if (!response.error) {
              message.success({ content: `${file.name} 上传成功`, key: uploadKey, duration: 2 });
              onSuccess(response);
              
              const isImage = file.type && file.type.startsWith('image/');
              
              if (isImage) {
                // 图片上传：显示图片并触发分析
                
                let userMessageContent;
                
                // 优先使用Circuit API返回的HTML内容
                if (response.content && response.content.includes('<img')) {
                  userMessageContent = response.content;
                } else if (response.content) {
                  userMessageContent = response.content;
                } else {
                  // 如果Circuit API没有返回HTML，尝试构建图片路径
                  userMessageContent = `📷 ${file.name}`;
                }
                
                
                // 验证会话ID
                if (!currentSessionId) {
                  message.error('会话ID无效，请刷新页面重试');
                  onError(new Error('会话ID无效'));
                  return;
                }
                
                // 保存并显示用户图片消息
                const userUploadMessage = {
                  content: userMessageContent,
                  messageId: maxMessageId + 1,
                  sessionId: currentSessionId,
                  userId: userId, // 直接使用，由addMessage内部处理
                  userType: MESSAGE_TYPE.USER,
                };

                await addMessage(userUploadMessage);
                
                // 添加用户消息到前端显示
                setMessages(prevMessages => {
                  return [...prevMessages, userUploadMessage];
                });
                
                // 延迟触发分析，确保用户消息保存完成
                setTimeout(() => {
                  if (!analyzing && !streaming) {
                    // 传递用户消息的messageId，确保bot消息使用正确的ID
                    startImageAnalysis(userUploadMessage.messageId);
                  } else {
                  }
                }, 1000); // 1秒延迟足够
              } else {
                // 非图片文件的处理
                const userUploadMessage = {
                  content: `文件 ${file.name} 上传`,
                  messageId: maxMessageId + 1,
                  sessionId: currentSessionId,
                  userId: userId,
                  userType: MESSAGE_TYPE.USER,
                };

                const botResponseMessage = {
                  content: `文件 ${file.name} 上传成功！`,
                  userType: MESSAGE_TYPE.BOT,
                  messageId: maxMessageId + 2,
                  sessionId: currentSessionId,
                  userId: userId,
                };

                await addMessage(userUploadMessage);
                setMessages(prevMessages => [...prevMessages, userUploadMessage, botResponseMessage]);

                // 保存BOT消息到数据库
                addMessage(botResponseMessage)
              }

            } else {
              message.error({ content: `${file.name} 上传失败: ${response.message}`, key: uploadKey, duration: 2 });
              onError(new Error(response.message));
            }
          })
          .catch(function (error) {
            message.error({ content: `${file.name} Circuit上传过程中出错`, key: uploadKey, duration: 2 });
            onError(error);
          })
          .finally(() => {
            setUploading(false);
          });
      } catch (error) {
        message.error("文件上传前准备工作出错，请稍后重试");
        onError(error);
        setUploading(false);
      }
    },
    [sessionId, userId, port, messages.length, handleStreamingChat, analyzing, streaming, startImageAnalysis],
  );

  const uploadProps = useMemo(() => ({
    listType: 'picture',
    multiple: false,
    showUploadList: false,
    beforeUpload,
    customRequest: ({ file, onSuccess, onError }) => onUploadFile(file, onSuccess, onError),
    accept: '.png,.jpg,.jpeg', // 限制文件选择器只显示支持的格式
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
  }), [onUploadFile]);

  // =============== 流式输出中止处理 ===============
  const handleAbortStream = useCallback(() => {
    if (streaming && streamRequestRef.current) {
      message.loading({ content: '正在中止...', key: 'abortMessage' });
      
      // 设置用户中断标志
      isUserAbortingRef.current = true;
      
      try {
        // 先更新UI状态，再调用abort
        setMessages(prev => {
          const updated = [...prev];
          for (let i = updated.length - 1; i >= 0; i--) {
            if (updated[i].userType === MESSAGE_TYPE.BOT && (updated[i].streaming || updated[i].isLoading)) {
              updated[i] = {
                ...updated[i],
                content: "对话已中断",
                streaming: false,
                isLoading: false
              };
              break;
            }
          }
          return updated;
        });
        
        // 重置状态
        setLoadingStates(false, false);
        setAnalyzing(false);
        stopThinkingAnimation();
        stopAnalyzingAnimation();
        
        // 最后调用abort，这样onError回调中的消息更新会被跳过
        streamRequestRef.current.abort();
        streamRequestRef.current = null;
        
        // 稍后重置中断标志
        setTimeout(() => {
          isUserAbortingRef.current = false;
        }, 100);
        
        message.success({ content: '已成功中止回答', key: 'abortMessage' });
      } catch (error) {
        resetStates();
        message.error({ content: '中止过程中出错，但已停止显示', key: 'abortMessage' });
      }
    }
  }, [streaming, setLoadingStates, stopThinkingAnimation, stopAnalyzingAnimation]);

  // =============== 生命周期管理 ===============
  useEffect(() => {
    return () => {
      if (streamRequestRef.current) {
        try {
          streamRequestRef.current.abort();
        } catch (e) {
        }
      }
      // 清理动态思考效果
      stopThinkingAnimation();
      stopAnalyzingAnimation();
    };
  }, [stopThinkingAnimation, stopAnalyzingAnimation]);

  // 自动滚动到底部
  useEffect(() => {
    const container = document.querySelector('.chat-message-list');
    if (container) {
      setTimeout(() => {
        container.scrollTop = container.scrollHeight;
      }, 100);
    }
  }, [messages]);

  // =============== 渲染 ===============
  return (
    <div className='tcad'>
      {!messages.length && (
        <div className='tcad-empty'>
          <div className='tcad-title'>您好，我是网表大模型</div>
          <div className='tcad-question'>有什么电路相关问题？</div>
          <div className='tcad-intro'>支持将电路图像转换为SPICE网表并解决电路相关问题</div>
          <div className='tcad-intro'>请上传您需要转换的电路图像并提问</div>
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
                type={item.type}
                imageUrl={item.imageUrl}
                fileInfo={item.fileInfo || (item.fileName ? {
                  name: item.fileName,
                  fileName: item.fileName
                } : undefined)}
                downloadUrl={item.fileUrl}
                messageId={item.messageId || item.message_id || index}
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
                placeholder='请输入您的问题...' 
                disabled={loading || streaming || uploading}
              />
            </Form.Item>
            
            <Upload {...uploadProps} fileList={fileList} disabled={loading || streaming}>
              <Button disabled={loading || streaming || uploading}>
                {uploading ? <LoadingOutlined /> : <CloudUploadOutlined />}
              </Button>
            </Upload>

            <div className='devide-line'></div>
            <Form.Item>
              {streaming ? (
                <Button
                  danger
                  onClick={handleAbortStream}
                  disabled={analyzing}
                  icon={<StopOutlined style={{ fontSize: '18px' }} />}
                  style={{ opacity: analyzing ? 0.5 : 1 }}
                >
                  中止
                </Button>
              ) : (
                <Button
                  disabled={loading || uploading}
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
        </div>
      </div>
    </div>
  );
};

export default CircuitThink;