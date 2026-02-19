import React, { useState } from 'react';
import { message } from 'antd';
import { CopyOutlined, CheckOutlined } from '@ant-design/icons';
import './CodeBlock.css';

const CodeBlock = ({ code, language, isStreaming }) => {
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    // 添加检查以确保navigator.clipboard存在
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(code).then(() => {
        setCopied(true);
        message.success('代码已复制到剪贴板');
        setTimeout(() => setCopied(false), 2000);
      }).catch(err => {
        console.error('复制失败:', err);
        message.error('复制失败');
        fallbackCopyTextToClipboard(code);
      });
    } else {
      // 回退方法
      fallbackCopyTextToClipboard(code);
    }
  };

  // 添加回退复制方法
  const fallbackCopyTextToClipboard = (text) => {
    try {
      const textArea = document.createElement('textarea');
      textArea.value = text;
      
      // 避免滚动到底部
      textArea.style.top = '0';
      textArea.style.left = '0';
      textArea.style.position = 'fixed';
      textArea.style.opacity = '0';
      
      document.body.appendChild(textArea);
      textArea.focus();
      textArea.select();
      
      const successful = document.execCommand('copy');
      document.body.removeChild(textArea);
      
      if (successful) {
        setCopied(true);
        message.success('代码已复制到剪贴板');
        setTimeout(() => setCopied(false), 2000);
      } else {
        message.error('复制失败');
      }
    } catch (err) {
      console.error('回退复制方法失败:', err);
      message.error('复制失败');
    }
  };

  return (
    <div className={`code-block-container ${isStreaming ? 'code-block-streaming' : ''}`}>
      <div className="code-block-header">
        <span className="code-language">{language || 'code'}</span>
        <button 
          className="copy-button" 
          onClick={handleCopy}
          aria-label="复制代码"
          disabled={isStreaming} // 在流式传输时禁用复制按钮
        >
          {copied ? <CheckOutlined /> : <CopyOutlined />}
          {copied ? ' 已复制' : ' 复制'}
        </button>
      </div>
      <pre className="code-block-content">
        <code>{code}{isStreaming && <span className="code-cursor"></span>}</code>
      </pre>
    </div>
  );
};

export default CodeBlock;