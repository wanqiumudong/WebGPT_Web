import './index.css'
import React, { useState, useEffect } from "react";
import Cookies from 'js-cookie';
import { Form, Input, Button, message } from 'antd';
import { fetchUploadImage, fetchUploadMessage } from '../../../api/fabGpt';
import { botInfo, MESSAGE_TYPE, THINKING_TEXTS, createDynamicBotInfo } from '../../../constants';
import ChatMessage from '../../../components/chatMessage';
// 导入默认会话相关函数
import { DEFAULT_SESSION, createRealSessionAfterChat, isDefaultSession } from '../../../components/history/history';

const API_BASE_URL = `http://10.98.64.22:8080`;


const FabGPT = () => {
  const [messages, setMessages] = useState([]);
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(false);
  const username = Cookies.get('user');
  const userId = Cookies.get('userid') || Cookies.get('userId');
  const [sessionId, setSessionId] = useState(Cookies.get(1));
  
  // 添加动态思考效果相关状态
  const [dynamicThinkingText, setDynamicThinkingText] = useState('思考中...');
  const thinkingIntervalRef = React.useRef(null);
  
  // 动态思考效果函数
  const startThinkingAnimation = React.useCallback(() => {
    if (thinkingIntervalRef.current) return; // 防止重复启动
    
    let textIndex = 0;
    thinkingIntervalRef.current = setInterval(() => {
      setDynamicThinkingText(THINKING_TEXTS[textIndex]);
      textIndex = (textIndex + 1) % THINKING_TEXTS.length;
    }, 500); // 每500ms切换一次
  }, []);
  
  const stopThinkingAnimation = React.useCallback(() => {
    if (thinkingIntervalRef.current) {
      clearInterval(thinkingIntervalRef.current);
      thinkingIntervalRef.current = null;
    }
    setDynamicThinkingText('思考中...'); // 重置为默认文本
  }, []);

  useEffect(() => {
    const handleSessionChange = () => {
      const newSessionId = Cookies.get(1);
      if (newSessionId !== sessionId) {
        setSessionId(newSessionId);
      }
    };

    const interval = setInterval(handleSessionChange, 1000);
    return () => clearInterval(interval);
  }, [sessionId]);

  useEffect(() => {
    if (sessionId && sessionId !== DEFAULT_SESSION) {
      // 只在真实session切换时才从服务器加载消息
      // 如果是在当前对话中创建的新session，不要清空当前消息
      fetch(`http://10.98.64.22:8080/message/list-by-session?sessionId=${sessionId}`)
        .then(response => response.json())
        .then(data => {
          // 只有当返回的消息数组不为空时才设置消息
          // 这样可以避免新创建的session覆盖当前对话
          if (Array.isArray(data) && data.length > 0) {
            // 转换后端消息格式为前端格式
            const convertedMessages = data.map(msg => ({
              content: msg.content,
              sender: msg.userType === 'user' ? MESSAGE_TYPE.USER : MESSAGE_TYPE.BOT,
              type: msg.content.includes('<img') ? 'html' : 'text'
            }));
            setMessages(convertedMessages);
          }
        })
        .catch(error => {
          // 出错时不要清空消息，保持当前状态
        });
    } else if (sessionId === DEFAULT_SESSION) {
      setMessages([]);
      // 清空localStorage中的旧消息，避免污染默认对话
      localStorage.removeItem('fbtMessages');
    }
  }, [sessionId]);



  const handleImageUpload = () => {
    if (loading) return;

    const input = document.createElement('input');
    input.type = 'file';
    input.accept = 'image/*';
    input.onchange = async (event) => {
      setLoading(true)

      const selectedFile = event.target.files[0];
      const submittedImageUrl = URL.createObjectURL(selectedFile);
      const newUploadMessage = {
        content: <img className="submitted-image" src={submittedImageUrl} alt="加载失败..." onError={null} />,
        sender: MESSAGE_TYPE.USER,
        type: 'img'
      }

      const submitMessages = messages.concat([newUploadMessage, createDynamicBotInfo(dynamicThinkingText)])

      setMessages(submitMessages);
      
      // 启动动态思考效果
      startThinkingAnimation();

      try {
        // 🔧 检查并创建真实会话
        let actualSessionId = sessionId;
        let sessionWasCreated = false;
        
        if (!actualSessionId || actualSessionId === DEFAULT_SESSION) {
          const newSessionId = await createRealSessionAfterChat(1);
          if (newSessionId) {
            actualSessionId = newSessionId;
            sessionWasCreated = true;
            setSessionId(newSessionId);
            Cookies.set(1, newSessionId, { expires: 7 });
          } else {
            setLoading(false);
            message.error('创建会话失败');
            return;
          }
        }

        // 创建一个 FormData 对象，用于发送文件到服务器
        const formData = new FormData();
        formData.append('image', selectedFile);  // 使用image字段名
        formData.append('user_input', '请分析这张图片');  // 添加用户输入
        formData.append('username', username || 'anonymous');  // 添加用户名

        const response = await fetchUploadImage(formData, actualSessionId);
        
        // 处理FabGPT的JSON响应格式
        let newBotMessage;
        if (response && typeof response === 'object' && response.content) {
          // FabGPT返回HTML格式的响应
          newBotMessage = {
            content: response.content, // 直接使用HTML字符串
            sender: MESSAGE_TYPE.BOT,
            type: 'html'
          };
        } else {
          // 处理其他格式
          newBotMessage = {
            content: response || "处理完成",
            sender: MESSAGE_TYPE.BOT,
            type: 'text'
          };
        }

        // 删除临时信息并替换用户图片为持久化URL
        const filterMessages = filterBotMessages(submitMessages);
        
        // 如果后端返回了original_image_url，更新用户消息中的图片URL为原始图片
        if (response && response.original_image_url) {
          const userMessage = filterMessages[filterMessages.length - 1]; // 最后一条用户消息
          if (userMessage && userMessage.type === 'img') {
            // 存储HTML字符串而不是React元素，避免序列化问题
            userMessage.content = `<img class="submitted-image" src="${response.original_image_url}" alt="用户上传的图片" style="max-width: 400px; max-height: 400px; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1);" />`;
            userMessage.type = 'html'; // 改为html类型
          }
        }
        
        const newMessages = filterMessages.concat(newBotMessage);
        localStorage.setItem('fbtMessages', JSON.stringify(newMessages));

        setMessages(newMessages);
        
        // 按照其他模块的模式保存消息到数据库
        try {
          // 获取当前最大消息ID
          const messageListResponse = await fetch(`${API_BASE_URL}/message/list-all`);
          let maxMessageId = 0;
          if (messageListResponse.ok) {
            const allMessages = await messageListResponse.json();
            maxMessageId = allMessages?.length ? Math.max(...allMessages.map(msg => msg.messageId)) : 0;
          }

          // 保存用户图片上传消息
          const userImageMessage = {
            content: `<img src="${response.original_image_url}" alt="用户上传的图片" style="max-width: 400px; max-height: 400px; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1);" />`,
            messageId: maxMessageId + 1,
            modelId: 1, // FabGPT的模型ID
            sessionId: actualSessionId,
            timestamp: new Date().toISOString(),
            userId: userId,
            userType: MESSAGE_TYPE.USER,
          };

          await fetch(`${API_BASE_URL}/message/add`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(userImageMessage),
          });

          // 保存AI响应消息
          const botResponseMessage = {
            content: response.content,
            messageId: maxMessageId + 2,
            modelId: 1, // FabGPT的模型ID
            sessionId: actualSessionId,
            timestamp: new Date().toISOString(),
            userId: userId,
            userType: MESSAGE_TYPE.BOT,
          };

          await fetch(`${API_BASE_URL}/message/add`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(botResponseMessage),
          });

            
          // 更新会话标题为"缺陷分析"
          const headerUpdate = {
            createTime: new Date().toISOString(),
            header: '缺陷分析',
            lastActive: new Date().toISOString(),
            modelId: 1,
            sessionId: actualSessionId,
            status: 1,
            userId: parseInt(userId),
          };

          await fetch(`${API_BASE_URL}/session/update`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(headerUpdate),
          });
          
          // 触发历史列表更新事件
          window.sessionUpdated = Date.now();
          window.dispatchEvent(new Event("sessionUpdated"));
        } catch (dbError) {
          }

        setLoading(false);
        
        // 停止动态思考效果
        stopThinkingAnimation();
      } catch (error) {
        const filterMessages = filterBotMessages(submitMessages);
        setMessages(filterMessages);
        setLoading(false);
        
        // 停止动态思考效果
        stopThinkingAnimation();
      }
    };
    input.click();
  };

  const filterBotMessages = (submitMessages) => {
    return submitMessages.filter((item) => !item.loading) || [];
  };

  const onhandleFinished = async () => {
    const values = await form.getFieldsValue();
    
    // 空状态
    if (!values?.content) {
      return;
    }
    

    setLoading(true);
    
    // 🔧 检查并创建真实会话
    let actualSessionId = sessionId;
    
    if (!actualSessionId || actualSessionId === DEFAULT_SESSION) {
      const newSessionId = await createRealSessionAfterChat(1);
      if (newSessionId) {
        actualSessionId = newSessionId;
        setSessionId(newSessionId);
        Cookies.set(1, newSessionId, { expires: 7 });
      } else {
        setLoading(false);
        message.error('创建会话失败');
        return;
      }
    }
    
    // 处理表单提交
    const newMessage = Object.assign(values, { sender: MESSAGE_TYPE.USER });
    const submitMessages = messages.concat([newMessage, createDynamicBotInfo(dynamicThinkingText)]);
    setMessages(submitMessages);
    
    // 启动动态思考效果
    startThinkingAnimation();

    // 重置表单
    await form.resetFields();

    try {
      
      const result = await fetchUploadMessage({
        message: values.content,
        username: username
      }, actualSessionId);
      const newBotMessage = {
        content: result,
        sender: MESSAGE_TYPE.BOT,
      };

      // 删除临时信息
      const filterMessages = filterBotMessages(submitMessages);
      const newMessages = filterMessages.concat(newBotMessage);
      localStorage.setItem('fbtMessages', JSON.stringify(newMessages));

      setMessages(newMessages);
      
      // 按照其他模块的模式保存消息到数据库
      try {
        // 获取当前最大消息ID
        const messageListResponse = await fetch(`${API_BASE_URL}/message/list-all`);
        let maxMessageId = 0;
        if (messageListResponse.ok) {
          const allMessages = await messageListResponse.json();
          maxMessageId = allMessages?.length ? Math.max(...allMessages.map(msg => msg.messageId)) : 0;
        }

        // 保存用户文本消息
        const userTextMessage = {
          content: values.content,
          messageId: maxMessageId + 1,
          modelId: 1, // FabGPT的模型ID
          sessionId: actualSessionId,
          timestamp: new Date().toISOString(),
          userId: userId,
          userType: MESSAGE_TYPE.USER,
        };

        await fetch(`${API_BASE_URL}/message/add`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(userTextMessage),
        });

        // 保存AI响应消息
        const botResponseMessage = {
          content: result,
          messageId: maxMessageId + 2,
          modelId: 1, // FabGPT的模型ID
          sessionId: actualSessionId,
          timestamp: new Date().toISOString(),
          userId: userId,
          userType: MESSAGE_TYPE.BOT,
        };

        await fetch(`${API_BASE_URL}/message/add`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(botResponseMessage),
        });

        
        // 更新会话标题为用户输入的前几个字
        const cleanContent = values.content.replace(/<[^>]*>/g, '');
        const generatedTitle = cleanContent.slice(0, 8) || '新对话';
        
        const headerUpdate = {
          createTime: new Date().toISOString(),
          header: generatedTitle,
          lastActive: new Date().toISOString(),
          modelId: 1,
          sessionId: actualSessionId,
          status: 1,
          userId: parseInt(userId),
        };

        await fetch(`${API_BASE_URL}/session/update`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(headerUpdate),
        });
        
        // 触发历史列表更新事件
        window.sessionUpdated = Date.now();
        window.dispatchEvent(new Event("sessionUpdated"));
      } catch (dbError) {
      }
      
      setLoading(false);
      
      // 停止动态思考效果
      stopThinkingAnimation();
    } catch (e) {
      const filterMessages = filterBotMessages(submitMessages);
      setMessages(filterMessages);
      setLoading(false);
      
      // 停止动态思考效果
      stopThinkingAnimation();
      
      return '请求出错啦';
    }
  };

  useEffect(() => {
    // 只有在非默认会话状态下才从localStorage加载消息
    if (sessionId && sessionId !== DEFAULT_SESSION) {
      const initMessages = localStorage.getItem('fbtMessages');
      if (!!initMessages) {
        try {
          const initContent = JSON.parse(initMessages);
          // 如果有img标签，渲染jsx组件
          initContent.forEach((item) =>
            item.content.type === 'img' ?
              item.content = React.createElement('img', { ...item.content.props }) : item.content
          );
          setMessages(initContent);
        } catch (e) { }
      }
    }
  }, [sessionId]); // 依赖sessionId，确保会话变化时重新执行

  useEffect(() => {
    const container = document.querySelector('.fab-gpt-message-list');
    if (!container) return;

    setTimeout(() => {
      container.scrollTop = container.scrollHeight;
    }, 100)
  }, [messages]);
  
  // 组件卸载时清理动画
  useEffect(() => {
    return () => {
      stopThinkingAnimation();
    };
  }, [stopThinkingAnimation]);

  return (
    <div className='fab-gpt-bot'>
      {!messages.length && (
        <div className='fab-gpt-empty'>
          <div className='fab-gpt-title'>你好，我是缺陷大模型</div>
          <div className='fab-gpt-question'>有什么相关问题吗？</div>
          <div className='fab-gpt-intro'>支持自动化晶圆缺陷检测、晶圆知识查询</div>
          <div className='fab-gpt-intro' style={{ marginTop: -10 }}>请上传您需要查询的晶圆图像</div>
        </div>
      )}
      {messages.length && (
        <div className='fab-gpt-message-list'>
          {messages.map((item, index) => (
            <ChatMessage
              key={index}
              sendType={item.sender}
              message={item.content}
              loading={item.loading}
              type={item.type}
            ></ChatMessage>
          ))}
        </div>
      )}
      <div className='fab-gpt-footer'>
        <Form
          form={form}
          layout='inline'
          style={{ width: '100%' }}
          onFinish={onhandleFinished}
          autoComplete='off'
        >
          <Form.Item name='content' style={{ width: 'calc(100% - 140px)' }}>
            <Input placeholder='尽管问...'></Input>
          </Form.Item>
          <img
            className='fab-gpt-upload-img'
            src={require('../../../assets/uploadImg.png')}
            onClick={handleImageUpload}
            disabled={loading}
            alt='upload'
          ></img>
          <div className='fab-gpt-devide-line'></div>
          <Form.Item>
            <Button
              disabled={loading}
              loading={loading}
              htmlType='submit'
              icon={
                <img
                  src={require('../../../assets/send.png')}
                  style={{ height: 32, width: 32 }}
                  alt='send'
                ></img>
              }
            ></Button>
          </Form.Item>
        </Form>
      </div>
    </div>
  );
}

export default FabGPT;