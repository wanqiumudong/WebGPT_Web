import './TcadWorkspaceDrawer.css';
import React, { useEffect, useMemo, useState } from 'react';
import { Button, Empty, Spin, Tag, message } from 'antd';
import {
  CopyOutlined,
  DownloadOutlined,
  DownOutlined,
  PictureOutlined,
  RightOutlined,
} from '@ant-design/icons';
import {
  buildTCADSessionExportUrl,
  fetchTCADSessionSummary,
  fetchTCADWorkspaceManifest,
  fetchTCADWorkspacePreview,
} from '../../api/tcadApi';
import { TCAD_BASE_URL } from '../../config/endpoints';

const buildDownloadUrl = (downloadPath, forceDownload = false) => {
  if (!downloadPath) {
    return '';
  }
  const url = `${TCAD_BASE_URL}${downloadPath}`;
  return forceDownload ? `${url}${url.includes('?') ? '&' : '?'}download=1` : url;
};

const fallbackCopyText = (text) => {
  const textArea = document.createElement('textarea');
  textArea.value = text;
  textArea.style.position = 'fixed';
  textArea.style.top = '0';
  textArea.style.left = '0';
  textArea.style.opacity = '0';
  document.body.appendChild(textArea);
  textArea.focus();
  textArea.select();
  const success = document.execCommand('copy');
  document.body.removeChild(textArea);
  return success;
};

const TcadWorkspaceDrawer = ({
  open,
  onToggle,
  userId,
  conversationId,
  refreshToken,
}) => {
  const [loading, setLoading] = useState(false);
  const [manifest, setManifest] = useState(null);
  const [sessionSummary, setSessionSummary] = useState(null);
  const [activeFileKey, setActiveFileKey] = useState('');
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewData, setPreviewData] = useState(null);
  const [collapsedGroups, setCollapsedGroups] = useState({ logs: true });

  useEffect(() => {
    let cancelled = false;

    const loadManifest = async () => {
      if (!userId || !conversationId) {
        setManifest(null);
        setActiveFileKey('');
        return;
      }

      setLoading(true);
      try {
        const [manifestResponse, summaryResponse] = await Promise.all([
          fetchTCADWorkspaceManifest({
            user_id: userId,
            conversation_id: conversationId,
          }),
          fetchTCADSessionSummary({
            user_id: userId,
            conversation_id: conversationId,
          }),
        ]);
        if (cancelled) {
          return;
        }
        const nextManifest = manifestResponse?.manifest || null;
        setManifest(nextManifest);
        setSessionSummary(summaryResponse?.summary || null);
        setCollapsedGroups((current) => ({
          ...current,
          logs: current.logs ?? true,
        }));
        if (!nextManifest?.groups) {
          setActiveFileKey('');
          return;
        }
        const allItems = nextManifest.groups.flatMap((group) => group.items || []);
        if (!allItems.length) {
          setActiveFileKey('');
          return;
        }
        setActiveFileKey((currentKey) => {
          if (currentKey && allItems.some((item) => item.key === currentKey)) {
            return currentKey;
          }
          return allItems[0].key;
        });
      } catch (error) {
        if (!cancelled) {
          setManifest(null);
          setSessionSummary(null);
          setActiveFileKey('');
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    };

    loadManifest();
    return () => {
      cancelled = true;
    };
  }, [conversationId, refreshToken, userId]);

  const allItems = useMemo(() => {
    if (!manifest?.groups) {
      return [];
    }
    return manifest.groups.flatMap((group) => group.items || []);
  }, [manifest]);

  const activeItem = useMemo(() => {
    return allItems.find((item) => item.key === activeFileKey) || null;
  }, [activeFileKey, allItems]);

  useEffect(() => {
    let cancelled = false;

    const loadPreview = async () => {
      if (!activeItem || !userId || !conversationId) {
        setPreviewData(null);
        return;
      }
      if (activeItem.is_image || !activeItem.previewable) {
        setPreviewData(null);
        return;
      }
      setPreviewLoading(true);
      try {
        const response = await fetchTCADWorkspacePreview({
          user_id: userId,
          conversation_id: conversationId,
          path: activeItem.relative_path,
          max_lines: 120,
        });
        if (!cancelled) {
          setPreviewData(response?.preview || null);
        }
      } catch (error) {
        if (!cancelled) {
          setPreviewData(null);
        }
      } finally {
        if (!cancelled) {
          setPreviewLoading(false);
        }
      }
    };

    loadPreview();
    return () => {
      cancelled = true;
    };
  }, [activeItem, conversationId, userId]);

  const toggleGroup = (groupKey) => {
    setCollapsedGroups((current) => ({
      ...current,
      [groupKey]: !current[groupKey],
    }));
  };

  const handleCopyPreview = async () => {
    if (!previewData?.content) {
      return;
    }
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(previewData.content);
      } else if (!fallbackCopyText(previewData.content)) {
        throw new Error('clipboard fallback failed');
      }
      message.success('已复制当前预览内容');
    } catch (error) {
      try {
        if (fallbackCopyText(previewData.content)) {
          message.success('已复制当前预览内容');
          return;
        }
      } catch (fallbackError) {
        // Ignore and fall through to the UI error message.
      }
      message.error('复制失败，请重试');
    }
  };

  const exportMarkdownUrl = useMemo(() => buildTCADSessionExportUrl({
    user_id: userId,
    conversation_id: conversationId,
    format: 'markdown',
  }), [conversationId, userId]);

  return (
    <div className={`tcad-workspace-drawer ${open ? 'tcad-workspace-drawer-open' : ''}`}>
      <div className="tcad-workspace-handle">
        <div className="tcad-workspace-handle-meta">
          <div className="tcad-workspace-handle-title">当前工作区</div>
          <div className="tcad-workspace-handle-subtitle">
            {manifest?.stage_label || '未开始执行'}
            {manifest?.latest_note ? ` · ${manifest.latest_note}` : ''}
          </div>
        </div>
        <div className="tcad-workspace-handle-actions">
          <Tag color="blue">主要文件 {manifest?.primary_file_count || 0}</Tag>
          {(manifest?.log_file_count || 0) > 0 ? (
            <Tag>日志 {manifest?.log_file_count || 0}</Tag>
          ) : null}
          {userId && conversationId ? (
            <Button
              type="link"
              onClick={() => window.open(exportMarkdownUrl, '_blank', 'noreferrer')}
            >
              导出摘要
            </Button>
          ) : null}
          <Button type="link" onClick={onToggle}>
            {open ? '收起工作区' : '打开工作区'}
          </Button>
        </div>
      </div>

      {open ? (
        <div className="tcad-workspace-body">
          <div className="tcad-workspace-sidebar">
            {sessionSummary ? (
              <div className="tcad-workspace-summary">
                <div className="tcad-workspace-summary-grid">
                  <div className="tcad-workspace-summary-item">
                    <span className="tcad-workspace-summary-label">当前阶段</span>
                    <strong>{sessionSummary.stage_label || '未开始'}</strong>
                  </div>
                  <div className="tcad-workspace-summary-item">
                    <span className="tcad-workspace-summary-label">验证状态</span>
                    <strong>{sessionSummary.validation_status_label || '未完成'}</strong>
                  </div>
                </div>
              </div>
            ) : null}
            <div className="tcad-workspace-sidebar-scroll">
              {loading ? (
                <div className="tcad-workspace-loading">
                  <Spin size="small" />
                </div>
              ) : null}
              {!loading && (!manifest?.groups || !allItems.length) ? (
                <Empty
                  image={Empty.PRESENTED_IMAGE_SIMPLE}
                  description="当前会话还没有可展示的工作区文件"
                />
              ) : null}
              {!loading && manifest?.groups?.map((group) => (
                group.items?.length ? (
                  <div key={group.key} className="tcad-workspace-group">
                    <button
                      type="button"
                      className="tcad-workspace-group-header"
                      onClick={() => toggleGroup(group.key)}
                    >
                      <span className="tcad-workspace-group-header-main">
                        {collapsedGroups[group.key] ? <RightOutlined /> : <DownOutlined />}
                        <span>{group.label}</span>
                      </span>
                      <span>{group.count}</span>
                    </button>
                    {!collapsedGroups[group.key] ? (
                      <div className="tcad-workspace-group-list">
                        {group.items.map((item) => (
                          <button
                            key={item.key}
                            type="button"
                            className={`tcad-workspace-file ${activeItem?.key === item.key ? 'tcad-workspace-file-active' : ''}`}
                            onClick={() => setActiveFileKey(item.key)}
                          >
                            <div className="tcad-workspace-file-label">{item.label}</div>
                            <div className="tcad-workspace-file-name">{item.file_name}</div>
                          </button>
                        ))}
                      </div>
                    ) : null}
                  </div>
                ) : null
              ))}
            </div>
          </div>

          <div className="tcad-workspace-preview">
            {!activeItem ? (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description="选择一个文件查看内容"
              />
            ) : (
              <React.Fragment>
                <div className="tcad-workspace-preview-header">
                  <div>
                    <div className="tcad-workspace-preview-title">{activeItem.label}</div>
                    <div className="tcad-workspace-preview-name">{activeItem.file_name}</div>
                  </div>
                  {!activeItem.is_image ? (
                    <div className="tcad-workspace-preview-actions">
                      {activeItem.previewable ? (
                        <Button
                          icon={<CopyOutlined />}
                          onClick={handleCopyPreview}
                        >
                          复制内容
                        </Button>
                      ) : null}
                      <Button
                        icon={<DownloadOutlined />}
                        onClick={() => window.open(buildDownloadUrl(activeItem.download_path, true), '_blank', 'noreferrer')}
                      >
                        下载
                      </Button>
                    </div>
                  ) : null}
                </div>

                {activeItem.is_image ? (
                  <div className="tcad-workspace-image-panel">
                    <Button
                      className="tcad-workspace-image-download"
                      type="primary"
                      size="small"
                      icon={<DownloadOutlined />}
                      onClick={() => window.open(buildDownloadUrl(activeItem.download_path, true), '_blank', 'noreferrer')}
                    >
                      下载
                    </Button>
                    <img
                      className="tcad-workspace-image"
                      src={buildDownloadUrl(activeItem.download_path)}
                      alt={activeItem.file_name}
                    />
                  </div>
                ) : null}

                {!activeItem.is_image && activeItem.previewable ? (
                  <div className="tcad-workspace-text-panel">
                    {previewLoading ? (
                      <div className="tcad-workspace-loading">
                        <Spin size="small" />
                      </div>
                    ) : (
                      <React.Fragment>
                        <div className="tcad-workspace-preview-badge">
                          <PictureOutlined />
                          <span>{previewData?.line_count || 0} 行</span>
                          {previewData?.truncated ? <Tag color="gold">已截断</Tag> : null}
                        </div>
                        <pre className="tcad-workspace-text">{previewData?.content || '暂无可预览内容'}</pre>
                      </React.Fragment>
                    )}
                  </div>
                ) : null}

                {!activeItem.is_image && !activeItem.previewable ? (
                  <div className="tcad-workspace-file-placeholder">
                    该文件类型暂不支持浏览，请使用“下载”查看。
                  </div>
                ) : null}
              </React.Fragment>
            )}
          </div>
        </div>
      ) : null}
    </div>
  );
};

export default TcadWorkspaceDrawer;
