import './index.css';
import React, { useEffect, useMemo, useCallback, useState, useRef } from 'react';
import { Button, Form, Input, message, Upload } from 'antd';
import Cookies from 'js-cookie';
import {
  uploadTCADFile,
  fetchTCADStreaming,
  fetchTCADSessionSummary,
} from '../../api/tcadApi';
import { MESSAGE_TYPE } from '../../constants';
import ChatMessage from '../../components/chatMessage';
import TcadWorkspaceDrawer from './TcadWorkspaceDrawer';
import { LoadingOutlined, CloudUploadOutlined, StopOutlined } from '@ant-design/icons';
// 导入默认会话相关函数
import { DEFAULT_SESSION, createRealSessionAfterChat } from '../../components/history/history';
import { BACKEND_BASE_URL, TCAD_BASE_URL } from '../../config/endpoints';

const MESSAGE_API_BASE_URL = BACKEND_BASE_URL;
const PERSISTED_ARTIFACT_HEADER = '结果文件：';
const PERSISTED_ARTIFACT_LINE = /^\s*-\s*\[([^\]]+)\]\(([^)]+)\)\s*$/;
const LEGACY_TCAD_SUMMARY_PREFIX = /^本轮执行(已完成|未完全成功)/;
const LEGACY_TCAD_ARTIFACT_BLOCK = /\n*关键产物：[\s\S]*$/;

const Chatbot = ({ port }) => {
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(false);
  const [messages, setMessages] = useState([]);
  const [uploading, setUploading] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const username = Cookies.get('user');
  const normalizeId = (value) => {
    if (value === null || value === undefined) return '';
    const text = String(value).trim();
    if (!text) return '';
    const lowered = text.toLowerCase();
    if (lowered === 'undefined' || lowered === 'null') return '';
    return text;
  };
  const userId = normalizeId(Cookies.get('userid')) || normalizeId(Cookies.get('userId'));
  const effectiveUserId = userId || normalizeId(username) || 'anonymous';
  const [fileList, setFileList] = useState([]);
  const [sessionId, setSessionId] = useState(Cookies.get('3')); // TCAD modelId = 3
  const [workspaceOpen, setWorkspaceOpen] = useState(false);
  const [workspaceRefreshToken, setWorkspaceRefreshToken] = useState(0);
  const nextClientMessageIdRef = useRef((Date.now() % 1000000000) * 10);
  const pendingSessionHydrationRef = useRef(null);
  const messageListRef = useRef(null);
  const currentFlowEntriesRef = useRef([]);
  const currentArtifactLinksRef = useRef([]);
  const currentAgentStatusRef = useRef('');
  const currentAssistantFlowIdRef = useRef(null);
  const normalizeArtifactLink = useCallback((artifact) => {
    if (!artifact || !artifact.download_path) {
      if (artifact?.url) {
        return artifact;
      }
      return artifact;
    }
    return {
      ...artifact,
      url: `${TCAD_BASE_URL}${artifact.download_path}`,
    };
  }, []);

  const normalizePersistedArtifactUrl = useCallback((rawUrl) => {
    if (!rawUrl || typeof rawUrl !== 'string') {
      return rawUrl;
    }

    if (rawUrl.startsWith('/proxy/tcad/')) {
      return rawUrl;
    }

    if (rawUrl.startsWith('/artifacts/')) {
      return `${TCAD_BASE_URL}${rawUrl}`;
    }

    const proxyPath = rawUrl.match(/^https?:\/\/[^/]+\/proxy\/tcad(\/.*)$/i);
    if (proxyPath?.[1]) {
      return `${TCAD_BASE_URL}${proxyPath[1]}`;
    }

    const artifactPath = rawUrl.match(/^https?:\/\/[^/]+\/artifacts(\/.*)$/i);
    if (artifactPath?.[1]) {
      return `${TCAD_BASE_URL}/artifacts${artifactPath[1]}`;
    }

    return rawUrl;
  }, []);

  const mergeArtifactLinks = useCallback((existingLinks, incomingLinks) => {
    const merged = new Map();
    [...(existingLinks || []), ...(incomingLinks || [])]
      .map((item) => normalizeArtifactLink(item))
      .filter(Boolean)
      .forEach((item) => {
        const dedupeKey = item.url || item.download_path || item.file_name || item.label || item.key;
        merged.set(dedupeKey, item);
      });
    return Array.from(merged.values());
  }, [normalizeArtifactLink]);

  const extractPersistedArtifactLinks = useCallback((content) => {
    if (typeof content !== 'string' || !content.includes(PERSISTED_ARTIFACT_HEADER)) {
      return { cleanContent: content, artifactLinks: [] };
    }

    const lines = content.split('\n');
    let headerIndex = -1;

    for (let index = lines.length - 1; index >= 0; index -= 1) {
      if (lines[index].trim() !== PERSISTED_ARTIFACT_HEADER) {
        continue;
      }
      const remainingLines = lines.slice(index + 1).filter((line) => line.trim() !== '');
      if (remainingLines.length > 0 && remainingLines.every((line) => PERSISTED_ARTIFACT_LINE.test(line))) {
        headerIndex = index;
        break;
      }
    }

    if (headerIndex === -1) {
      return { cleanContent: content, artifactLinks: [] };
    }

    const artifactLines = lines
      .slice(headerIndex + 1)
      .map((line) => line.trim())
      .filter(Boolean);

    const artifactLinks = artifactLines
      .map((line, index) => {
        const match = line.match(PERSISTED_ARTIFACT_LINE);
        if (!match) {
          return null;
        }

        const label = match[1].trim();
        const url = normalizePersistedArtifactUrl(match[2].trim());
        const fileName = decodeURIComponent(url.split('/').pop() || label);

        return {
          key: `persisted-${index}-${label}`,
          label,
          file_name: fileName,
          url,
          is_image: /\.(png|jpg|jpeg|gif|webp)$/i.test(fileName),
        };
      })
      .filter(Boolean);

    const cleanContent = lines
      .slice(0, headerIndex)
      .join('\n')
      .trim();

    return { cleanContent, artifactLinks };
  }, [normalizePersistedArtifactUrl]);

  const stripLegacySummaryArtifacts = useCallback((content) => {
    if (typeof content !== 'string') {
      return content;
    }
    const normalized = content.trim();
    if (!LEGACY_TCAD_SUMMARY_PREFIX.test(normalized)) {
      return content;
    }
    return normalized.replace(LEGACY_TCAD_ARTIFACT_BLOCK, '').trim();
  }, []);

  const hydrateLegacyTcadMessage = useCallback((messageItem, summary) => {
    if (!messageItem || messageItem.userType !== MESSAGE_TYPE.BOT) {
      return messageItem;
    }
    const content = typeof messageItem.content === 'string' ? messageItem.content.trim() : '';
    if (!LEGACY_TCAD_SUMMARY_PREFIX.test(content)) {
      return messageItem;
    }

    const flowEntries = Array.isArray(messageItem.flowEntries) ? [...messageItem.flowEntries] : [];
    const toolSequence = Array.isArray(summary?.tool_sequence) ? summary.tool_sequence : [];
    const referenceSummary = typeof summary?.reference_summary_note === 'string'
      ? summary.reference_summary_note.trim()
      : '';
    const latestNote = typeof summary?.latest_note === 'string' ? summary.latest_note.trim() : '';

    if (!flowEntries.length && referenceSummary) {
      flowEntries.push({
        id: 'legacy-reference-summary',
        kind: 'note',
        text: referenceSummary,
      });
    }

    if (!flowEntries.length && toolSequence.length > 0) {
      toolSequence.forEach((toolName, index) => {
        flowEntries.push({
          id: `legacy-tool-${index}-${toolName}`,
          kind: 'tool_end',
          label: `${toolName} 调用完成`,
        });
      });
    }

    if (
      latestNote &&
      latestNote !== referenceSummary &&
      !flowEntries.some((entry) => (entry.text || entry.label || '') === latestNote)
    ) {
      flowEntries.push({
        id: 'legacy-latest-note',
        kind: 'note',
        text: latestNote,
      });
    }

    return {
      ...messageItem,
      content: stripLegacySummaryArtifacts(content),
      flowEntries: flowEntries.slice(-24),
      artifactLinks: mergeArtifactLinks(messageItem.artifactLinks, summary?.artifacts || []),
    };
  }, [mergeArtifactLinks, stripLegacySummaryArtifacts]);

  const normalizePersistedMessage = useCallback((messageItem) => {
    if (!messageItem || typeof messageItem !== 'object') {
      return messageItem;
    }

    const nextMessage = { ...messageItem };

    if (typeof nextMessage.fileInfo === 'string') {
      try {
        nextMessage.fileInfo = JSON.parse(nextMessage.fileInfo);
      } catch (error) {
        nextMessage.fileInfo = undefined;
      }
    }

    if (nextMessage.userType === MESSAGE_TYPE.BOT && typeof nextMessage.content === 'string') {
      const { cleanContent, artifactLinks } = extractPersistedArtifactLinks(nextMessage.content);
      nextMessage.content = stripLegacySummaryArtifacts(cleanContent);
      nextMessage.artifactLinks = mergeArtifactLinks(nextMessage.artifactLinks, artifactLinks);
    }

    if (nextMessage.userType === MESSAGE_TYPE.BOT && nextMessage.fileInfo && typeof nextMessage.fileInfo === 'object') {
      if (Array.isArray(nextMessage.fileInfo.tcadFlowEntries) && nextMessage.fileInfo.tcadFlowEntries.length > 0) {
        nextMessage.flowEntries = nextMessage.fileInfo.tcadFlowEntries;
      }
      if (Array.isArray(nextMessage.fileInfo.tcadArtifactLinks) && nextMessage.fileInfo.tcadArtifactLinks.length > 0) {
        nextMessage.artifactLinks = mergeArtifactLinks(
          nextMessage.artifactLinks,
          nextMessage.fileInfo.tcadArtifactLinks,
        );
      }
      if (typeof nextMessage.fileInfo.tcadAgentStatus === 'string' && nextMessage.fileInfo.tcadAgentStatus.trim()) {
        nextMessage.agentStatus = nextMessage.fileInfo.tcadAgentStatus.trim();
      }
    }

    return nextMessage;
  }, [extractPersistedArtifactLinks, mergeArtifactLinks, stripLegacySummaryArtifacts]);

  const updateLatestBotMessage = useCallback((updater) => {
    setMessages((prevMessages) => {
      const updatedMessages = [...prevMessages];
      for (let index = updatedMessages.length - 1; index >= 0; index -= 1) {
        if (updatedMessages[index].userType === MESSAGE_TYPE.BOT) {
          updatedMessages[index] = updater(updatedMessages[index]);
          return updatedMessages;
        }
      }
      return prevMessages;
    });
  }, []);

  const scrollMessagesToBottom = useCallback(() => {
    requestAnimationFrame(() => {
      const container = messageListRef.current;
      if (!container) {
        return;
      }
      container.scrollTop = container.scrollHeight;
    });
  }, []);

  const appendTraceEntry = useCallback((entry) => {
    const traceEntry = {
      id: `${Date.now()}-${Math.random().toString(16).slice(2, 8)}`,
      ...entry
    };
    updateLatestBotMessage((messageItem) => ({
      ...messageItem,
      traceEntries: [...(messageItem.traceEntries || []), traceEntry].slice(-18)
    }));
  }, [updateLatestBotMessage]);

  const appendFlowEntry = useCallback((entry) => {
    const flowEntry = {
      id: `${Date.now()}-${Math.random().toString(16).slice(2, 8)}`,
      ...entry,
    };
    const currentEntries = currentFlowEntriesRef.current || [];
    const lastEntry = currentEntries[currentEntries.length - 1];
    const comparableText = flowEntry.text || flowEntry.label || '';
    const lastComparableText = lastEntry?.text || lastEntry?.label || '';
    if (
      !lastEntry ||
      lastEntry.kind !== flowEntry.kind ||
      lastComparableText !== comparableText
    ) {
      currentFlowEntriesRef.current = [...currentEntries, flowEntry].slice(-24);
    }
    updateLatestBotMessage((messageItem) => {
      return {
        ...messageItem,
        flowEntries: currentFlowEntriesRef.current,
      };
    });
  }, [updateLatestBotMessage]);

  const resetAssistantFlowSegment = useCallback(() => {
    currentAssistantFlowIdRef.current = null;
  }, []);

  const appendAssistantFlowChunk = useCallback((chunk) => {
    const text = String(chunk || '');
    if (!text) {
      return;
    }

    const currentEntries = currentFlowEntriesRef.current || [];
    const currentAssistantId = currentAssistantFlowIdRef.current;

    if (currentAssistantId) {
      const existingIndex = currentEntries.findIndex((item) => item.id === currentAssistantId);
      if (existingIndex >= 0) {
        const nextEntries = [...currentEntries];
        nextEntries[existingIndex] = {
          ...nextEntries[existingIndex],
          text: `${nextEntries[existingIndex].text || ''}${text}`,
        };
        currentFlowEntriesRef.current = nextEntries.slice(-24);
        updateLatestBotMessage((messageItem) => ({
          ...messageItem,
          flowEntries: currentFlowEntriesRef.current,
        }));
        return;
      }
      currentAssistantFlowIdRef.current = null;
    }

    const entryId = `assistant-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`;
    currentAssistantFlowIdRef.current = entryId;
    currentFlowEntriesRef.current = [
      ...currentEntries,
      {
        id: entryId,
        kind: 'assistant_text',
        text,
      },
    ].slice(-24);
    updateLatestBotMessage((messageItem) => ({
      ...messageItem,
      flowEntries: currentFlowEntriesRef.current,
    }));
  }, [updateLatestBotMessage]);

  const upsertFlowEntry = useCallback((entry) => {
    if (!entry?.id) {
      appendFlowEntry(entry);
      return;
    }

    const currentEntries = currentFlowEntriesRef.current || [];
    const nextEntries = [...currentEntries];
    const existingIndex = nextEntries.findIndex((item) => item.id === entry.id);

    if (existingIndex >= 0) {
      nextEntries[existingIndex] = {
        ...nextEntries[existingIndex],
        ...entry,
      };
    } else {
      nextEntries.push(entry);
    }

    currentFlowEntriesRef.current = nextEntries.slice(-24);
    updateLatestBotMessage((messageItem) => ({
      ...messageItem,
      flowEntries: currentFlowEntriesRef.current,
    }));
  }, [appendFlowEntry, updateLatestBotMessage]);

  const formatPlanStepText = useCallback((step) => {
    const status = String(step?.status || 'pending');
    const title = step?.title || step?.tool_name || '未命名步骤';
    const statusMap = {
      pending: '待执行',
      in_progress: '执行中',
      completed: '已完成',
      failed: '失败',
      skipped: '已跳过',
      blocked: '阻塞',
    };
    return `${statusMap[status] || status} · ${title}`;
  }, []);

  const buildToolFlowEntryId = useCallback((payload) => {
    if (payload?.event_id) {
      return `tool-${payload.event_id}`;
    }
    const reason = String(payload?.reason || '').trim();
    const toolName = String(payload?.tool_name || 'tool').trim();
    const stage = String(payload?.stage || '').trim();
    if (reason) {
      return `tool-${reason}`;
    }
    return `tool-${toolName}-${stage}`;
  }, []);

  const findLatestToolFlowEntryId = useCallback((toolName) => {
    const entries = currentFlowEntriesRef.current || [];
    for (let index = entries.length - 1; index >= 0; index -= 1) {
      const entry = entries[index];
      if (
        entry?.kind === 'tool_status' &&
        String(entry?.toolName || '').trim() === String(toolName || '').trim()
      ) {
        return entry.id;
      }
    }
    return '';
  }, []);

  const updateAgentStatus = useCallback((status) => {
    currentAgentStatusRef.current = status || '';
    updateLatestBotMessage((messageItem) => ({
      ...messageItem,
      agentStatus: status
    }));
  }, [updateLatestBotMessage]);

  const refreshWorkspace = useCallback(() => {
    setWorkspaceRefreshToken(Date.now());
  }, []);
  
  const apiRequest = useCallback(async (endpoint, options = {}) => {
    const url = `${MESSAGE_API_BASE_URL}/${endpoint}`;
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
  }, []);

  // TCAD会话标题更新函数
  const updateSessionHeader = useCallback(async (sessionId, userId, userMessage = '', botMessage = '') => {
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
  }, [apiRequest]);

  const createClientMessageId = useCallback(() => {
    nextClientMessageIdRef.current += 2;
    if (nextClientMessageIdRef.current > 2000000000) {
      nextClientMessageIdRef.current = 2;
    }
    return nextClientMessageIdRef.current;
  }, []);

  const ensureActiveSessionId = useCallback(async (preferredSessionId) => {
    const normalizedSessionId = preferredSessionId || Cookies.get('3');

    if (!normalizedSessionId || normalizedSessionId === DEFAULT_SESSION) {
      const newSessionId = await createRealSessionAfterChat(3);
      if (!newSessionId) {
        throw new Error('创建会话失败');
      }
      pendingSessionHydrationRef.current = newSessionId;
      setSessionId(newSessionId);
      return newSessionId;
    }

    try {
      const existingSession = await apiRequest(`session/get?sessionId=${encodeURIComponent(normalizedSessionId)}`);
      if (existingSession && existingSession.sessionId) {
        return normalizedSessionId;
      }
    } catch (error) {
      if (!String(error?.message || '').includes('Session not exists')) {
        throw error;
      }
    }

    const recreatedSessionId = await createRealSessionAfterChat(3);
    if (!recreatedSessionId) {
      throw new Error('创建会话失败');
    }
    pendingSessionHydrationRef.current = recreatedSessionId;
    setSessionId(recreatedSessionId);
    return recreatedSessionId;
  }, [apiRequest]);
  
  
  // 新增：用于存储流式请求引用
  const streamRequestRef = useRef(null);
  
  const fileUploadTimesRef = useRef({});

  const [deletedFiles, setDeletedFiles] = useState(() => {
    try {
      const saved = localStorage.getItem(`deletedFiles_${sessionId}`);
      return saved ? JSON.parse(saved) : {};
    } catch (e) {
      return {};
    }
  });

  useEffect(() => {
    const handleSessionChange = () => {
      const newSessionId = Cookies.get('3');
      if (newSessionId !== sessionId) {
        pendingSessionHydrationRef.current = null;
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
      const { modelId, shouldResetToDefault } = event.detail;
      
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
    if (!sessionId || sessionId === DEFAULT_SESSION) {
      setWorkspaceOpen(false);
      refreshWorkspace();
      return;
    }
    refreshWorkspace();
  }, [refreshWorkspace, sessionId]);

  useEffect(() => {
    if (sessionId && sessionId !== DEFAULT_SESSION) {
      const shouldPreserveCurrentMessages = pendingSessionHydrationRef.current === sessionId;
      fetch(`${MESSAGE_API_BASE_URL}/message/list-by-session?sessionId=${encodeURIComponent(sessionId)}`)
        .then(async (response) => {
          if (!response.ok) {
            const errorText = await response.text();
            throw new Error(`HTTP error! status: ${response.status}, body: ${errorText}`);
          }
          return response.json();
        })
        .then(async data => {
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
          
          let normalizedMessages = filteredData.map((item) => normalizePersistedMessage(item));
          const needsLegacyHydration = normalizedMessages.some(
            (item) =>
              item?.userType === MESSAGE_TYPE.BOT &&
              typeof item.content === 'string' &&
              LEGACY_TCAD_SUMMARY_PREFIX.test(item.content.trim()) &&
              !(Array.isArray(item.flowEntries) && item.flowEntries.length > 0),
          );

          if (needsLegacyHydration) {
            try {
              const summaryPayload = await fetchTCADSessionSummary({
                user_id: username || effectiveUserId,
                conversation_id: sessionId,
              });
              const summary = summaryPayload?.summary || summaryPayload;
              normalizedMessages = normalizedMessages.map((item) => hydrateLegacyTcadMessage(item, summary));
            } catch (error) {
              console.warn('恢复旧版TCAD会话摘要失败:', error);
            }
          }

          setMessages(prevMessages => {
            const hasStreamingMessage = prevMessages.some(msg => msg.streaming);
            const shouldKeepLocalMessages =
              shouldPreserveCurrentMessages && prevMessages.length > normalizedMessages.length;

            if (hasStreamingMessage || shouldKeepLocalMessages) {
              return prevMessages;
            }

            pendingSessionHydrationRef.current = null;
            return normalizedMessages;
          });
        })
        .catch(error => {
          setMessages(prevMessages => {
            const hasStreamingMessage = prevMessages.some(msg => msg.streaming);
            if (hasStreamingMessage || shouldPreserveCurrentMessages) {
              return prevMessages;
            }
            return [];
          });
        });
    } else if (sessionId === DEFAULT_SESSION) {
      pendingSessionHydrationRef.current = null;
      setMessages([]);
    } else {
      pendingSessionHydrationRef.current = null;
      setMessages([]);
    }
  }, [sessionId, deletedFiles, effectiveUserId, hydrateLegacyTcadMessage, normalizePersistedMessage, username]);

  const onhandleFinished = async (overridePayload = null) => {
    const values = await form.getFieldsValue();
    const messageContent = String(overridePayload?.content || values?.content || '').trim();
    const demoCaseId = String(overridePayload?.demoCaseId || '').trim();
    
    if (!messageContent) return;
    
    setLoading(true);
    setStreaming(true);
    
    let actualSessionId = sessionId;

    try {
      actualSessionId = await ensureActiveSessionId(sessionId);
    } catch (error) {
      setLoading(false);
      setStreaming(false);
      message.error('创建会话失败');
      return;
    }
    
    const userMessageId = createClientMessageId();
    const botMessageId = userMessageId + 1;
    
    const newMessage = {
      content: messageContent,
      messageId: userMessageId,
      modelId: 3,
      sessionId: actualSessionId,
      timestamp: new Date().toISOString(),
      userId: userId ? parseInt(userId) : 1, // 确保userId是数字类型
      userType: MESSAGE_TYPE.USER,
    };
    
    const tempBotMessage = {
      content: '',
      messageId: botMessageId,
      modelId: 3,
      sessionId: actualSessionId,
      timestamp: new Date().toISOString(),
      userId: userId ? parseInt(userId) : 1, // 确保userId是数字类型
      userType: MESSAGE_TYPE.BOT,
      streaming: true,
      isLoading: true,
      agentStatus: '',
      traceEntries: [],
      flowEntries: []
    };

    currentFlowEntriesRef.current = [];
    currentArtifactLinksRef.current = [];
    currentAgentStatusRef.current = '';

    setMessages(prevMessages => [...prevMessages, newMessage, tempBotMessage]);
    scrollMessagesToBottom();
    await form.resetFields();

    fetch(`${MESSAGE_API_BASE_URL}/message/add`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(newMessage),
    }).then((response) => {
      if (!response.ok) {
        console.error('保存用户消息失败:', response.status);
      }
    }).catch((error) => {
      console.error('保存用户消息时出错:', error);
    });
    
    const params = { 
      user_id: username || effectiveUserId, 
      message: messageContent,
      conversation_id: actualSessionId,
      demo_case_id: demoCaseId || undefined,
    };
    
    try {
      let fullResponse = '';
      let latestArtifactLinks = [];
      let streamAborted = false;
      
      // 将fetchTCADStreaming的返回值存储到ref中
      streamRequestRef.current = fetchTCADStreaming(
        params, 
        (data) => {
          if (data) {
            // 检查是否有中止标志
            if (data.aborted) {
              streamAborted = true;
              updateLatestBotMessage((messageItem) => ({
                ...messageItem,
                content: `${messageItem.content || ''}\n\n[已中止回答]`.trim(),
                streaming: false,
                isAborted: true,
                isLoading: false,
                agentStatus: '执行已中止'
              }));
              
              setStreaming(false);
              setLoading(false);
              appendTraceEntry({ kind: 'aborted', label: '执行已中止' });
              message.success({ content: '已中止当前回答', key: 'abortMessage' });
              return;
            }
            
            if (data.kind === 'start' || data.start_streaming) {
              resetAssistantFlowSegment();
              currentAgentStatusRef.current = '';
              updateLatestBotMessage((messageItem) => ({
                ...messageItem,
                request_id: data.request_id,
                isLoading: false,
                agentStatus: '',
              }));
              return;
            }

            if (data.kind === 'status') {
              resetAssistantFlowSegment();
              const hasNaturalStatus = Boolean(data.message);
              const statusLabel = data.message || (() => {
                if (data.status === 'queued') {
                  const scopeText = data.scope === 'conversation' ? '同一会话' : '全局任务池';
                  const positionText = data.queue_position ? `，排队位置 ${data.queue_position}` : '';
                  return `${scopeText}正在执行其他任务，当前请求已排队${positionText}`;
                }
                if (data.status === 'running') {
                  return '';
                }
                return '状态已更新。';
              })();
              if (statusLabel) {
                updateAgentStatus(statusLabel);
              }
              if (hasNaturalStatus && statusLabel) {
                appendFlowEntry({
                  kind: 'note',
                  text: statusLabel,
                });
              }
              return;
            }

            if (data.kind === 'tool_start') {
              resetAssistantFlowSegment();
              upsertFlowEntry({
                id: buildToolFlowEntryId(data),
                kind: 'tool_status',
                status: 'running',
                toolName: data.tool_name || '',
                label: `正在调用 ${data.tool_name} MCP`,
              });
              return;
            }

            if (data.kind === 'plan_created') {
              resetAssistantFlowSegment();
              upsertFlowEntry({
                id: `plan-summary-${data.plan_id || 'current'}`,
                kind: 'plan_created',
                label: '执行计划',
              });
              if (Array.isArray(data.plan_steps)) {
                data.plan_steps.forEach((step) => {
                  upsertFlowEntry({
                    id: `plan-step-${data.plan_id || 'current'}-${step.step_id || step.tool_name}`,
                    kind: 'plan_step',
                    status: step.status,
                    title: step.title || step.tool_name || '未命名步骤',
                    toolName: step.tool_name || '',
                    text: formatPlanStepText(step),
                  });
                });
              }
              return;
            }

            if (data.kind === 'plan_step_update') {
              resetAssistantFlowSegment();
              upsertFlowEntry({
                id: `plan-step-${data.plan_id || 'current'}-${data.step_id || data.tool_name}`,
                kind: 'plan_step',
                status: data.status,
                title: data.title || data.tool_name || '未命名步骤',
                toolName: data.tool_name || '',
                text: formatPlanStepText(data),
              });
              return;
            }

            if (data.kind === 'plan_replanned') {
              resetAssistantFlowSegment();
              appendFlowEntry({
                kind: 'plan_replanned',
                text: '后续执行计划已根据失败结果自动重规划。',
              });
              if (Array.isArray(data.plan_steps)) {
                data.plan_steps.forEach((step) => {
                  upsertFlowEntry({
                    id: `plan-step-${data.plan_id || 'current'}-${step.step_id || step.tool_name}`,
                    kind: 'plan_step',
                    text: formatPlanStepText(step),
                  });
                });
              }
              return;
            }

            if (data.kind === 'plan_completed') {
              return;
            }

            if (data.kind === 'tool_end') {
              resetAssistantFlowSegment();
              upsertFlowEntry({
                id: findLatestToolFlowEntryId(data.tool_name) || buildToolFlowEntryId(data),
                kind: 'tool_status',
                status: data.ok ? 'success' : 'error',
                toolName: data.tool_name || '',
                label: `${data.tool_name} ${data.ok ? '调用成功' : '调用失败'}`,
              });
              return;
            }

            if (data.kind === 'artifact') {
              resetAssistantFlowSegment();
              const incomingArtifacts = data.artifact_download_path ? [{
                key: data.artifact_key,
                label: data.artifact_label || data.artifact_key,
                file_name: data.artifact_path,
                download_path: data.artifact_download_path,
                is_image: Boolean(data.is_image),
              }] : [];
              updateLatestBotMessage((messageItem) => ({
                ...messageItem,
                artifactLinks: mergeArtifactLinks(messageItem.artifactLinks, incomingArtifacts),
              }));
              latestArtifactLinks = mergeArtifactLinks(latestArtifactLinks, incomingArtifacts);
              currentArtifactLinksRef.current = latestArtifactLinks;
              updateAgentStatus(`已生成产物 ${data.artifact_key}`);
              refreshWorkspace();
              return;
            }

            if (data.kind === 'done') {
              resetAssistantFlowSegment();
              const mergedArtifacts = mergeArtifactLinks([], data.artifacts || []);
              latestArtifactLinks = mergeArtifactLinks(latestArtifactLinks, mergedArtifacts);
              currentArtifactLinksRef.current = latestArtifactLinks;
              const replyText = String(fullResponse || data.assistant_reply || '').trim();
              fullResponse = replyText;
              currentAgentStatusRef.current = data.aborted ? '执行已中止' : '';
              updateLatestBotMessage((messageItem) => ({
                ...messageItem,
                content: fullResponse,
                artifactLinks: latestArtifactLinks,
                agentStatus: data.aborted ? '执行已中止' : '',
                isLoading: false,
              }));
              refreshWorkspace();
              return;
            }

            if (data.kind === 'error') {
              resetAssistantFlowSegment();
              appendFlowEntry({
                kind: 'error',
                text: data.error || '执行失败',
              });
              currentAgentStatusRef.current = data.error || '执行失败';
              updateLatestBotMessage((messageItem) => ({
                ...messageItem,
                content: fullResponse || '本次 TCAD 处理失败，请查看下方调用记录。',
                isLoading: false,
                agentStatus: data.error || '执行失败'
              }));
              message.error(data.error || 'TCAD执行失败');
              return;
            }
            
            if (data.chunk) {
              fullResponse += data.chunk;
              appendAssistantFlowChunk(data.chunk);
              
              setMessages(prevMessages => {
                const updatedMessages = [...prevMessages];
                const lastIndex = updatedMessages.length - 1;
                
                if (lastIndex >= 0 && updatedMessages[lastIndex].streaming) {
                  if (updatedMessages[lastIndex].isLoading) {
                    updatedMessages[lastIndex] = {
                      ...updatedMessages[lastIndex],
                      content: data.chunk,
                      isLoading: false,
                      agentStatus: ''
                    };
                  } else {
                    updatedMessages[lastIndex] = {
                      ...updatedMessages[lastIndex],
                      content: fullResponse,
                      agentStatus: ''
                    };
                  }
                }
                
                return updatedMessages;
              });
              scrollMessagesToBottom();
            }
          }
        },
        async () => {
          setStreaming(false);
          setLoading(false);
          resetAssistantFlowSegment();

          updateLatestBotMessage((messageItem) => ({
            ...messageItem,
            streaming: false,
            isLoading: false,
            agentStatus: '',
            artifactLinks: mergeArtifactLinks(messageItem.artifactLinks, []),
          }));

          if (streamAborted) {
            return;
          }
          
          const finalBotMessage = {
            content: fullResponse,
            messageId: botMessageId,
            modelId: 3,
            sessionId: actualSessionId,
            timestamp: new Date().toISOString(),
            userId: userId ? parseInt(userId) : 1, // 确保userId是数字类型
            userType: MESSAGE_TYPE.BOT,
            fileInfo: JSON.stringify({
              tcadFlowEntries: currentFlowEntriesRef.current,
              tcadArtifactLinks: currentArtifactLinksRef.current,
              tcadAgentStatus: currentAgentStatusRef.current,
            }),
          };
          
          const botSaveResult = await fetch(`${MESSAGE_API_BASE_URL}/message/add`, {
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
            await updateSessionHeader(actualSessionId, userId, messageContent, fullResponse);
            if (demoCaseId) {
              refreshWorkspace();
            }
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
                isError: true,
                agentStatus: errorMessage
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

        currentSessionId = await ensureActiveSessionId(currentSessionId);
        Cookies.set(3, currentSessionId, { expires: 7 });

        var formData = new FormData();
        formData.append('file', file);
        formData.append('type', file.type);
        formData.append('conversation_id', currentSessionId);
        formData.append('user_id', username || effectiveUserId); // 添加用户ID

        const messageListResponse = await fetch(`${MESSAGE_API_BASE_URL}/message/list-all`);
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
          const saveResponse = await fetch(`${MESSAGE_API_BASE_URL}/message/add`, {
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
          onUploadProgress: function () {},
        })
          .then((response) => {
            if (response?.status === 200) {
              message.success({ content: `${file.name} 上传成功`, key: uploadKey, duration: 2 });
              onSuccess(response);
              refreshWorkspace();
              
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
              scrollMessagesToBottom();

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
              
              fetch(`${MESSAGE_API_BASE_URL}/message/add`, {
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
              scrollMessagesToBottom();

              fetch(`${MESSAGE_API_BASE_URL}/message/add`, {
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
            scrollMessagesToBottom();

            fetch(`${MESSAGE_API_BASE_URL}/message/add`, {
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
    [sessionId, userId, username, effectiveUserId, updateSessionHeader, ensureActiveSessionId, refreshWorkspace, scrollMessagesToBottom],
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

  return (
    <div className='tcad'>
      {!messages.length && (
        <div className='tcad-empty'>
          <div className='tcad-title'>您好，我是TCAD大模型</div>
          <div className='tcad-question'>有什么相关问题吗？</div>
          <div className='tcad-intro'>
            面向SDE代码生成任务的网页平台。
            <br />
            支持自然语言需求输入、SDE代码生成、结构检查、电学仿真、紧凑建模与结果展示。
          </div>
        </div>
      )}
      {messages.length > 0 && (
        <div className="tcad-content-shell">
          <div className="tcad-chat-shell">
            <div className="tcad-chat-pane">
              <div className="chat-message-list" ref={messageListRef}>
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
                      isDeleted={item.isDeleted}
                      deleted={item.deleted}
                      isSystemPrompt={item.isSystemPrompt}
                      traceEntries={item.traceEntries}
                      flowEntries={item.flowEntries}
                      traceTitle={item.traceTitle}
                      agentStatus={item.agentStatus}
                    />
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}
      {sessionId && sessionId !== DEFAULT_SESSION ? (
        <TcadWorkspaceDrawer
          open={workspaceOpen}
          onToggle={() => setWorkspaceOpen((current) => !current)}
          userId={username || effectiveUserId}
          conversationId={sessionId}
          refreshToken={workspaceRefreshToken}
        />
      ) : null}
      <div className='tcad-footer'>
        <div style={{ display: 'flex', width: '100%', alignItems: 'center' }}>
          <Form
            form={form}
            layout='inline'
            style={{ flex: 1, display: 'flex', alignItems: 'center' }}
            onFinish={() => onhandleFinished()}
            autoComplete='off'
          >
            <Form.Item name='content' style={{ flex: 1, margin: '0 10px 0 0' }}>
              <Input 
                placeholder='尽管问...' 
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
                  icon={<StopOutlined style={{ fontSize: '18px' }} />}
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
                    ></img>
                  }
                ></Button>
              )}
            </Form.Item>
          </Form>
        </div>
      </div>
    </div>
  );
};

export default Chatbot;
