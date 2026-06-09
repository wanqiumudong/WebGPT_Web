import React, { useMemo } from 'react';
import { Avatar, Button, Tooltip } from 'antd';
import { MESSAGE_AVATAR, MESSAGE_TYPE } from '../../constants';
import { DeleteOutlined, DownloadOutlined } from '@ant-design/icons';
import './index.css';
import classNames from 'classnames';
import MarkdownIt from 'markdown-it';
import CodeBlock from './CodeBlock';

const md = new MarkdownIt({
  html: true,
  breaks: true,
});

// 完整代码块的正则表达式
const completeCodeBlockRegex = /```(\w*)\n([\s\S]*?)```/g;

// 代码块开始标记的正则表达式
const codeBlockStartRegex = /```(\w*)\n/;

// 解析消息内容，分离普通文本和代码块，增加对流式传输中未完成代码块的支持
const parseContent = (content, isStreaming) => {
  if (!content) return [];
  
  const parts = [];
  let lastIndex = 0;
  let match;
  
  // 重置正则表达式的lastIndex
  completeCodeBlockRegex.lastIndex = 0;
  
  // 先处理完整的代码块
  while ((match = completeCodeBlockRegex.exec(content)) !== null) {
    // 添加代码块前的普通文本
    if (match.index > lastIndex) {
      parts.push({
        type: 'text',
        content: content.substring(lastIndex, match.index)
      });
    }
    
    // 添加完整的代码块
    parts.push({
      type: 'code',
      language: match[1] || 'code',
      content: match[2].trim(),
      isComplete: true
    });
    
    lastIndex = match.index + match[0].length;
  }
  
  // 如果正在流式传输，检查是否有未完成的代码块
  if (isStreaming) {
    const remainingContent = content.substring(lastIndex);
    const startMatch = remainingContent.match(codeBlockStartRegex);
    
    if (startMatch) {
      // 添加从上一个位置到代码块开始之前的普通文本
      const startIndex = lastIndex + startMatch.index;
      if (startIndex > lastIndex) {
        parts.push({
          type: 'text',
          content: content.substring(lastIndex, startIndex)
        });
      }
      
      // 添加未完成的代码块
      const incompleteCodeStart = startIndex + startMatch[0].length;
      parts.push({
        type: 'code',
        language: startMatch[1] || 'code',
        content: content.substring(incompleteCodeStart),
        isComplete: false,
        isStreaming: true
      });
      
      lastIndex = content.length;
    }
  }
  
  // 添加最后一部分文本
  if (lastIndex < content.length) {
    parts.push({
      type: 'text',
      content: content.substring(lastIndex)
    });
  }
  
  // 如果没有找到任何内容，返回整个内容作为文本
  if (parts.length === 0) {
    parts.push({
      type: 'text',
      content: content
    });
  }
  
  return parts;
};

const ChatMessage = (props) => {
  const { 
    sendType, 
    message, 
    loading, 
    streaming, 
    type = null, 
    fileInfo = null,
    onFileDelete = null,
    messageId = null,
    downloadUrl = null,
    isDeleted = false,
    deleted = false,
    isLoading = false,
    traceEntries = [],
    flowEntries = [],
    agentStatus = '',
    artifactLinks = []
  } = props;
  
  const avatarUrl = MESSAGE_AVATAR[sendType];
  
  // 是否为图片消息
  const isImgUrl = useMemo(() => {
    return type === 'img';
  }, [type]);
  
  // 是否为文件消息
  const isFileMessage = useMemo(() => {
    return type === 'file';
  }, [type]);
  
  // 流式消息支持
  const isStreaming = Boolean(streaming);
  const hasTracePanel = sendType === MESSAGE_TYPE.BOT && traceEntries.length > 0;
  const hasFlowPanel = sendType === MESSAGE_TYPE.BOT && flowEntries.length > 0;
  const hasAssistantTimeline = sendType === MESSAGE_TYPE.BOT && flowEntries.some(
    (entry) => entry.kind === 'assistant_text' || entry.kind === 'assistant'
  );
  
  // === 直接处理不同类型的内容 ===
  const contentParts = useMemo(() => {
    // 如果是图片或文件消息，直接返回
    if (isImgUrl || isFileMessage) {
      return [{ type: 'direct', content: message }];
    }
    
    // 如果message不是字符串（可能是React元素），直接返回
    if (typeof message !== 'string') {
      return [{ type: 'direct', content: message }];
    }
    
    // 默认：普通文本，使用现有的markdown解析逻辑
    return parseContent(message, isStreaming);
  }, [message, isImgUrl, isFileMessage, isStreaming]);
  
  // 删除文件删除状态检测
  const isFileDeleted = isDeleted || deleted;
  
  // 处理文件撤回
  const handleDeleteFile = (e) => {
    if (e) e.stopPropagation();
    if (onFileDelete && messageId) {
      // 确保转换为整数
      const numericMessageId = typeof messageId === 'string' ? parseInt(messageId, 10) : messageId;
      onFileDelete(numericMessageId, fileInfo);
    }
  };
  
  // === 简化：渲染不同类型的内容部分 ===
  const renderContentPart = (part, index) => {
    switch (part.type) {
      case 'code':
        return (
          <CodeBlock 
            key={index} 
            code={part.content} 
            language={part.language} 
            isStreaming={part.isStreaming}
          />
        );
        
      case 'direct':
        // 直接内容：React元素或简单文本
        return (
          <div key={index} className="message-direct-content">
            {part.content}
          </div>
        );
        
      case 'text':
      default:
        // 普通文本：使用markdown处理（保持原有逻辑）
        try {
          const htmlContent = md.render(part.content);
          return (
            <div key={index} className="message-text-content">
              <div dangerouslySetInnerHTML={{ __html: htmlContent }} style={{ display: 'inline' }} />
            </div>
          );
        } catch (error) {
          // 如果markdown解析出错，回退到纯文本显示
          console.warn('Markdown解析出错，回退到纯文本显示:', error);
          return (
            <div key={index} className="message-text-content">
              <span>{part.content}</span>
            </div>
          );
        }
    }
  };

  const renderTracePanel = () => {
    if (!hasTracePanel) {
      return null;
    }

    return (
      <div className="message-trace-panel">
        <div className="message-trace-body">
          {traceEntries.map((entry) => (
            <div key={entry.id} className={`message-trace-item message-trace-${entry.kind || 'status'}`}>
              <div className="message-trace-item-label">{entry.label}</div>
              {entry.path ? <div className="message-trace-item-meta">{entry.path}</div> : null}
            </div>
          ))}
        </div>
      </div>
    );
  };

  const renderArtifactPanel = () => {
    if (!artifactLinks || artifactLinks.length === 0) {
      return null;
    }

    return (
      <div className="message-artifact-panel">
        <div className="message-artifact-title">结果文件</div>
        <div className="message-artifact-list">
          {artifactLinks.map((item) => (
            <a
              key={item.key || item.url || item.file_name}
              className="message-artifact-card"
              href={item.url}
              target="_blank"
              rel="noreferrer"
            >
              <div className="message-artifact-label">{item.label || item.file_name || item.key}</div>
              <div className="message-artifact-name">{item.file_name || item.key}</div>
            </a>
          ))}
        </div>
      </div>
    );
  };

  const renderPlanEntry = (entry) => {
    if (entry.kind === 'plan_step') {
      const status = String(entry.status || '').trim() || 'pending';
      const statusMap = {
        pending: '待执行',
        in_progress: '执行中',
        completed: '已完成',
        failed: '失败',
        skipped: '已跳过',
        blocked: '阻塞',
      };
      return (
        <div
          key={entry.id}
          className={classNames(
            'message-plan-step',
            `message-plan-step-${status}`
          )}
        >
          <span className="message-plan-step-state">{statusMap[status] || status}</span>
          <span className="message-plan-step-title">{entry.title || entry.text || '未命名步骤'}</span>
        </div>
      );
    }

    if (entry.kind === 'tool_status') {
      const status = String(entry.status || 'running').trim() || 'running';
      const statusMap = {
        running: '调用中',
        success: '调用成功',
        error: '调用失败',
      };
      return (
        <div
          key={entry.id}
          className={classNames(
            'message-tool-status',
            `message-tool-status-${status}`
          )}
        >
          <span className="message-tool-status-badge">{statusMap[status] || status}</span>
          <span className="message-tool-status-title">{entry.toolName || entry.label || '工具调用'}</span>
        </div>
      );
    }

    if (entry.kind === 'assistant_text' || entry.kind === 'assistant') {
      let htmlContent = '';
      try {
        htmlContent = md.render(entry.text || entry.label || '');
      } catch (error) {
        htmlContent = md.renderInline(entry.text || entry.label || '');
      }

      return (
        <div key={entry.id} className="message-flow-assistant-text">
          <div dangerouslySetInnerHTML={{ __html: htmlContent }} />
        </div>
      );
    }

    return (
      <div
        key={entry.id}
        className={`message-flow-item message-flow-${entry.kind || 'note'}`}
      >
        {entry.text || entry.label}
      </div>
    );
  };

  const renderFlowPanel = () => {
    const visibleFlowEntries = flowEntries;
    const planEntries = visibleFlowEntries.filter(
      (entry) => entry.kind === 'plan_created' || entry.kind === 'plan_step'
    );
    const otherEntries = visibleFlowEntries.filter(
      (entry) => entry.kind !== 'plan_created' && entry.kind !== 'plan_step'
    );
    const planSummaryEntry = planEntries.find((entry) => entry.kind === 'plan_created');
    const planSteps = planEntries.filter((entry) => entry.kind === 'plan_step');
    const completedSteps = planSteps.filter((entry) => entry.status === 'completed').length;

    if (!visibleFlowEntries.length) {
      return null;
    }

    return (
      <div className="message-flow-panel">
        {planEntries.length > 0 ? (
          <details className="message-plan-details">
            <summary className="message-plan-summary">
              <span className="message-plan-summary-title">
                {planSummaryEntry?.label || '执行计划'}
              </span>
              <span className="message-plan-summary-meta">
                {completedSteps}/{planSteps.length || 0}
              </span>
            </summary>
            <div className="message-plan-list">
              {planSteps.map((entry) => renderPlanEntry(entry))}
            </div>
          </details>
        ) : null}
        {otherEntries.map((entry) => renderPlanEntry(entry))}
      </div>
    );
  };
  
  // 渲染部分
  return (
    <div
      className={classNames(
        'chat-message',
        sendType === MESSAGE_TYPE.USER && 'chat-message-right'
      )}
    >
      <Avatar shape='square' size={48} src={avatarUrl}></Avatar>
      <div className={classNames(
        'message-content',
        isImgUrl && 'message-img-content',
        isFileMessage && 'message-file-content',
        (loading || isStreaming) && 'message-loading',
        (loading || isStreaming) && hasFlowPanel && 'message-loading-with-flow'
      )}>
        {/* 消息内容渲染 */}
        {isLoading ? (
          <React.Fragment>
            {!hasTracePanel && !hasFlowPanel && (agentStatus || message) ? (
              <div className="loading-message">
                <span>{agentStatus || message}</span>
              </div>
            ) : null}
            {renderFlowPanel()}
            {renderTracePanel()}
            {renderArtifactPanel()}
          </React.Fragment>
        ) : (
        isFileMessage ? (
          <div className="file-message-container">
            <div className="file-message">
              {isFileDeleted ? (
                <span className="file-deleted">
                  {message}
                </span>
              ) : (
                <span>{message}</span>
              )}
            </div>
            {!isFileDeleted && (
              <div className="file-actions">
                {downloadUrl && (
                  <Tooltip title="下载文件">
                    <Button
                      type="text"
                      icon={<DownloadOutlined />}
                      size="small"
                      onClick={() => window.open(downloadUrl)}
                    />
                  </Tooltip>
                )}
                {sendType === MESSAGE_TYPE.USER && onFileDelete && (
                  <Tooltip title="撤回文件">
                    <Button 
                      type="text" 
                      className="file-delete-btn"
                      icon={<DeleteOutlined />} 
                      size="small"
                      onClick={handleDeleteFile}
                    />
                  </Tooltip>
                )}
              </div>
            )}
          </div>
        ) : isImgUrl ? (
          // === 图片消息渲染 ===
          <div className="image-message-container">
            {typeof message === 'string' ? (
              <span>{message}</span>
            ) : (
              <div>{message}</div>
            )}
          </div>
        ) : (
          // === 普通消息渲染 ===
          <React.Fragment>
            {!hasAssistantTimeline ? (
              <div className="message-content-wrapper">
                {contentParts.map((part, index) => renderContentPart(part, index))}
              </div>
            ) : null}
            {renderFlowPanel()}
            {renderTracePanel()}
            {renderArtifactPanel()}
          </React.Fragment>
        ))}
      </div>
    </div>
  );
};

export default ChatMessage;
