import React, { useEffect, useMemo, useState } from 'react';
import { Button, Empty, Spin, Tag } from 'antd';
import {
  fetchTCADArtifactPreview,
  fetchTCADBriefSummary,
  fetchTCADReferencePreview,
  fetchTCADSessionSummary,
  fetchTCADValidationSummary,
} from '../../api/tcadApi';
import { TCAD_BASE_URL } from '../../config/endpoints';
import './TcadWorkbench.css';

const IMAGE_TYPES = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg']);

const normalizeArtifact = (artifact) => {
  if (!artifact) {
    return null;
  }
  const downloadPath = artifact.download_path || artifact.downloadPath || '';
  return {
    ...artifact,
    url: artifact.url || (downloadPath ? `${TCAD_BASE_URL}${downloadPath}` : ''),
  };
};

const mergeArtifacts = (summaryArtifacts = [], liveArtifacts = []) => {
  const merged = new Map();
  [...summaryArtifacts, ...liveArtifacts]
    .map(normalizeArtifact)
    .filter(Boolean)
    .forEach((artifact) => {
      const key =
        artifact.url ||
        artifact.download_path ||
        artifact.file_name ||
        artifact.label ||
        artifact.key;
      merged.set(key, artifact);
    });
  return Array.from(merged.values());
};

const extractReferenceList = (summary) => {
  const groups = [
    ...(summary?.selected_sde_references || []).map((item) => ({
      ...item,
      display_group: 'SDE 参考',
    })),
    ...(summary?.selected_sdevice_references || []).map((item) => ({
      ...item,
      display_group: 'Full-flow / SDevice 参考',
    })),
    ...(summary?.selected_function_references || []).map((item) => ({
      ...item,
      display_group: '函数知识',
    })),
  ];
  return groups;
};

const formatMetricValue = (value) => {
  if (value === null || value === undefined || value === '') {
    return '—';
  }
  if (typeof value === 'boolean') {
    return value ? 'True' : 'False';
  }
  return String(value);
};

const TcadWorkbench = ({
  hasSession = false,
  sessionId = '',
  userId = '',
  artifactLinks = [],
  refreshToken = '',
}) => {
  const [loading, setLoading] = useState(false);
  const [summary, setSummary] = useState(null);
  const [briefs, setBriefs] = useState([]);
  const [validation, setValidation] = useState(null);
  const [artifactPreview, setArtifactPreview] = useState(null);
  const [artifactPreviewLoading, setArtifactPreviewLoading] = useState(false);
  const [referencePreview, setReferencePreview] = useState(null);
  const [referencePreviewLoading, setReferencePreviewLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;

    if (!hasSession || !sessionId || !userId) {
      setSummary(null);
      setBriefs([]);
      setValidation(null);
      setArtifactPreview(null);
      setReferencePreview(null);
      setLoading(false);
      return undefined;
    }

    const loadWorkbench = async () => {
      setLoading(true);
      const params = {
        user_id: userId,
        conversation_id: sessionId,
      };
      const [summaryResult, briefResult, validationResult] = await Promise.allSettled([
        fetchTCADSessionSummary(params),
        fetchTCADBriefSummary(params),
        fetchTCADValidationSummary(params),
      ]);

      if (cancelled) {
        return;
      }

      setSummary(summaryResult.status === 'fulfilled' ? summaryResult.value.summary || null : null);
      setBriefs(briefResult.status === 'fulfilled' ? briefResult.value.briefs || [] : []);
      setValidation(validationResult.status === 'fulfilled' ? validationResult.value || null : null);
      setLoading(false);
    };

    loadWorkbench();
    return () => {
      cancelled = true;
    };
  }, [hasSession, refreshToken, sessionId, userId]);

  const mergedArtifacts = useMemo(
    () => mergeArtifacts(summary?.artifacts || [], artifactLinks || []),
    [artifactLinks, summary]
  );

  const imageArtifacts = useMemo(
    () => mergedArtifacts.filter((item) => item.is_image || IMAGE_TYPES.has((item.file_type || '').toLowerCase())),
    [mergedArtifacts]
  );

  const fileArtifacts = useMemo(
    () => mergedArtifacts.filter((item) => !imageArtifacts.includes(item)),
    [imageArtifacts, mergedArtifacts]
  );

  const references = useMemo(() => extractReferenceList(summary), [summary]);
  const latestNotes = summary?.notes_tail || [];
  const uploads = summary?.uploads || [];
  const metrics = validation?.metrics || {};
  const demoCase = summary?.demo_case || null;
  const demoPanels = summary?.demo_panels || [];

  const handleArtifactPreview = async (artifactKey) => {
    if (!artifactKey || !sessionId || !userId) {
      return;
    }
    setArtifactPreviewLoading(true);
    setReferencePreview(null);
    try {
      const payload = await fetchTCADArtifactPreview({
        user_id: userId,
        conversation_id: sessionId,
        artifact_key: artifactKey,
        max_lines: 80,
      });
      setArtifactPreview(payload.preview || null);
    } catch (error) {
      console.error('获取产物预览失败:', error);
      setArtifactPreview(null);
    } finally {
      setArtifactPreviewLoading(false);
    }
  };

  const handleReferencePreview = async (refId) => {
    if (!refId || !sessionId || !userId) {
      return;
    }
    setReferencePreviewLoading(true);
    setArtifactPreview(null);
    try {
      const payload = await fetchTCADReferencePreview({
        user_id: userId,
        conversation_id: sessionId,
        ref_id: refId,
      });
      setReferencePreview(payload.reference || null);
    } catch (error) {
      console.error('获取参考预览失败:', error);
      setReferencePreview(null);
    } finally {
      setReferencePreviewLoading(false);
    }
  };

  if (!hasSession) {
    return (
      <aside className="tcad-workbench">
        <div className="tcad-workbench-header">
          <div>
            <div className="tcad-workbench-title">结果工作台</div>
            <div className="tcad-workbench-subtitle">发送任务后，这里会显示阶段、关键结果、脚本与结构图。</div>
          </div>
        </div>
        <div className="tcad-workbench-card">
          <Empty
            description="当前还没有运行中的 TCAD 会话"
            image={Empty.PRESENTED_IMAGE_SIMPLE}
          />
        </div>
      </aside>
    );
  }

  return (
    <aside className="tcad-workbench">
      <div className="tcad-workbench-header">
        <div>
          <div className="tcad-workbench-title">结果工作台</div>
          <div className="tcad-workbench-subtitle">在这里查看当前任务阶段、任务说明、结果文件和验证摘要。</div>
        </div>
      </div>

      <div className="tcad-workbench-card">
        {loading ? (
          <div className="tcad-workbench-preview-loading">
            <Spin size="small" />
            <span>正在同步当前会话信息...</span>
          </div>
        ) : (
          <>
            <div className="tcad-workbench-card-title">当前任务</div>
            <div className="tcad-workbench-stage-row">
              <Tag color="blue">{summary?.stage_label || '运行中'}</Tag>
              {validation?.status === 'passed' ? <Tag color="green">Validation Passed</Tag> : null}
              {validation?.status === 'failed' ? <Tag color="red">Validation Failed</Tag> : null}
            </div>
            <div className="tcad-workbench-stat-grid">
              <div className="tcad-workbench-stat-card">
                <div className="tcad-workbench-stat-label">Artifacts</div>
                <div className="tcad-workbench-stat-value">{mergedArtifacts.length}</div>
              </div>
              <div className="tcad-workbench-stat-card">
                <div className="tcad-workbench-stat-label">References</div>
                <div className="tcad-workbench-stat-value">{references.length}</div>
              </div>
              <div className="tcad-workbench-stat-card">
                <div className="tcad-workbench-stat-label">Uploads</div>
                <div className="tcad-workbench-stat-value">{uploads.length}</div>
              </div>
            </div>
            {summary?.requirement_short ? (
              <div className="tcad-workbench-requirement">{summary.requirement_short}</div>
            ) : (
              <div className="tcad-workbench-empty-text">当前还没有记录到任务需求摘要。</div>
            )}
            {summary?.reference_summary_note ? (
              <div className="tcad-workbench-note" style={{ marginTop: 10 }}>
                {summary.reference_summary_note}
              </div>
            ) : null}
            {demoCase?.title ? (
              <div className="tcad-workbench-demo-summary">
                <div className="tcad-workbench-demo-title">{demoCase.title}</div>
                {demoCase.summary ? (
                  <div className="tcad-workbench-demo-copy">{demoCase.summary}</div>
                ) : null}
                {demoCase.capabilities?.length ? (
                  <div className="tcad-workbench-demo-capabilities">
                    {demoCase.capabilities.map((capability) => (
                      <span key={capability} className="tcad-workbench-demo-chip">
                        {capability}
                      </span>
                    ))}
                  </div>
                ) : null}
              </div>
            ) : null}
            {uploads.length ? (
              <div className="tcad-workbench-upload-list">
                {uploads.map((item) => (
                  <div key={`${item.file_name}-${item.role}`} className="tcad-workbench-upload-chip">
                    {item.file_name} · {item.role || 'upload'}
                  </div>
                ))}
              </div>
            ) : null}
          </>
        )}
      </div>

      {demoPanels.length ? (
        <div className="tcad-workbench-card">
          <div className="tcad-workbench-card-title">任务说明</div>
          <div className="tcad-workbench-panel-list">
            {demoPanels.map((panel, index) => (
              <div key={`${panel.title || 'panel'}-${index}`} className="tcad-workbench-panel-card">
                <div className="tcad-workbench-panel-title">{panel.title || `面板 ${index + 1}`}</div>
                {(panel.items || []).map((item, itemIndex) => (
                  <div key={`${panel.title || index}-${itemIndex}`} className="tcad-workbench-panel-item">
                    {item}
                  </div>
                ))}
              </div>
            ))}
          </div>
        </div>
      ) : null}

      {latestNotes.length ? (
        <div className="tcad-workbench-card">
          <div className="tcad-workbench-card-title">最近进展</div>
          <div className="tcad-workbench-note-timeline">
            {latestNotes.map((item, index) => (
              <div key={`${index}-${item}`} className="tcad-workbench-note-step">
                <span className="tcad-workbench-note-dot" />
                <div className="tcad-workbench-note-line">{item}</div>
              </div>
            ))}
          </div>
        </div>
      ) : null}

      {references.length ? (
        <div className="tcad-workbench-card">
          <div className="tcad-workbench-card-title">相关资料</div>
          <div className="tcad-workbench-reference-list">
            {references.map((item) => (
              <div key={item.ref_id} className="tcad-workbench-reference-card">
                <div className="tcad-workbench-reference-head">
                  <div>
                    <div className="tcad-workbench-artifact-label">{item.title || item.ref_id}</div>
                    <div className="tcad-workbench-artifact-name">
                      {(item.display_group || item.source_label || item.source_kind || 'reference')}
                      {item.family ? ` · ${item.family}` : ''}
                    </div>
                  </div>
                  <Button size="small" onClick={() => handleReferencePreview(item.ref_id)}>
                    查看参考
                  </Button>
                </div>
                {item.why_matched ? (
                  <div className="tcad-workbench-reference-why">{item.why_matched}</div>
                ) : null}
              </div>
            ))}
          </div>
        </div>
      ) : null}

      {briefs.length ? (
        <div className="tcad-workbench-card">
          <div className="tcad-workbench-card-title">任务简报</div>
          <div className="tcad-workbench-briefs">
            {briefs.map((item) => (
              <div key={`${item.kind}-${item.file_name}`} className="tcad-workbench-brief-card">
                <div className="tcad-workbench-brief-title">{item.label}</div>
                <div className="tcad-workbench-brief-summary">{item.summary}</div>
                {item.highlights?.length ? (
                  <div className="tcad-workbench-highlight-list">
                    {item.highlights.map((highlight, index) => (
                      <div key={`${item.kind}-${index}`} className="tcad-workbench-highlight-item">
                        {highlight}
                      </div>
                    ))}
                  </div>
                ) : null}
              </div>
            ))}
          </div>
        </div>
      ) : null}

      {validation?.available ? (
        <div className="tcad-workbench-card">
          <div className="tcad-workbench-card-title">验证摘要</div>
          <div className="tcad-workbench-stage-row">
            <Tag color={validation.status === 'passed' ? 'green' : validation.status === 'failed' ? 'red' : 'blue'}>
              {validation.status || 'unknown'}
            </Tag>
          </div>
          {Object.keys(metrics).length ? (
            <div className="tcad-workbench-metrics">
              {Object.entries(metrics).slice(0, 6).map(([key, value]) => (
                <div key={key} className="tcad-workbench-metric-item">
                  <span className="tcad-workbench-metric-key">{key}</span>
                  <span className="tcad-workbench-metric-value">{formatMetricValue(value)}</span>
                </div>
              ))}
            </div>
          ) : (
            <div className="tcad-workbench-empty-text">当前没有额外的验证指标。</div>
          )}
        </div>
      ) : null}

      {mergedArtifacts.length ? (
        <div className="tcad-workbench-card">
          <div className="tcad-workbench-card-title">结果文件</div>
          <div className="tcad-workbench-artifacts">
            {imageArtifacts.length ? (
              <div className="tcad-workbench-gallery">
                {imageArtifacts.map((artifact) => (
                  <a
                    key={artifact.url || artifact.file_name}
                    className="tcad-workbench-gallery-card"
                    href={artifact.url}
                    target="_blank"
                    rel="noreferrer"
                  >
                    <img
                      className="tcad-workbench-artifact-image"
                      src={artifact.url}
                      alt={artifact.label || artifact.file_name}
                    />
                    <div className="tcad-workbench-gallery-caption">
                      <div className="tcad-workbench-artifact-label">{artifact.label || artifact.file_name}</div>
                      <div className="tcad-workbench-artifact-name">{artifact.file_name}</div>
                    </div>
                  </a>
                ))}
              </div>
            ) : null}
            {fileArtifacts.length ? (
              <div className="tcad-workbench-file-list">
                {fileArtifacts.map((artifact) => (
                  <div key={artifact.url || artifact.file_name} className="tcad-workbench-artifact-card">
                    <div className="tcad-workbench-artifact-head">
                      <div>
                        <div className="tcad-workbench-artifact-label">{artifact.label || artifact.file_name}</div>
                        <div className="tcad-workbench-artifact-name">{artifact.file_name}</div>
                      </div>
                      <a
                        className="tcad-workbench-artifact-link"
                        href={artifact.url}
                        target="_blank"
                        rel="noreferrer"
                      >
                        下载
                      </a>
                    </div>
                    <Button size="small" onClick={() => handleArtifactPreview(artifact.key)}>
                      查看预览
                    </Button>
                  </div>
                ))}
              </div>
            ) : null}
          </div>
        </div>
      ) : null}

      {(artifactPreviewLoading || artifactPreview) ? (
        <div className="tcad-workbench-card">
          <div className="tcad-workbench-card-title">产物预览</div>
          {artifactPreviewLoading ? (
            <div className="tcad-workbench-preview-loading">
              <Spin size="small" />
              <span>正在加载预览...</span>
            </div>
          ) : artifactPreview ? (
            <>
              <div className="tcad-workbench-preview-title">{artifactPreview.file_name}</div>
              <pre className="tcad-workbench-preview-content">{artifactPreview.content || '当前产物不支持文本预览。'}</pre>
            </>
          ) : null}
        </div>
      ) : null}

      {(referencePreviewLoading || referencePreview) ? (
        <div className="tcad-workbench-card">
          <div className="tcad-workbench-card-title">参考预览</div>
          {referencePreviewLoading ? (
            <div className="tcad-workbench-preview-loading">
              <Spin size="small" />
              <span>正在加载参考内容...</span>
            </div>
          ) : referencePreview ? (
            <div className="tcad-workbench-reference-preview">
              <div className="tcad-workbench-preview-title">{referencePreview.title}</div>
              <div className="tcad-workbench-note">{referencePreview.summary}</div>
              {referencePreview.why_matched ? (
                <div className="tcad-workbench-reference-why" style={{ marginTop: 10 }}>
                  {referencePreview.why_matched}
                </div>
              ) : null}
              {referencePreview.prompt_excerpt ? (
                <>
                  <div className="tcad-workbench-preview-title" style={{ marginTop: 12 }}>Prompt 摘要</div>
                  <pre className="tcad-workbench-preview-content">{referencePreview.prompt_excerpt}</pre>
                </>
              ) : null}
              {referencePreview.code_excerpt ? (
                <>
                  <div className="tcad-workbench-preview-title" style={{ marginTop: 12 }}>代码片段</div>
                  <pre className="tcad-workbench-preview-content">{referencePreview.code_excerpt}</pre>
                </>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}
    </aside>
  );
};

export default TcadWorkbench;
