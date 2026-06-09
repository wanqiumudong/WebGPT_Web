import React from 'react';
import { Button, Empty, Spin, Tag } from 'antd';
import './TcadDemoCases.css';

const TcadDemoCases = ({
  cases = [],
  loading = false,
  running = false,
  onUseCase = null,
}) => {
  if (loading) {
    return (
      <div className="tcad-demo-cases tcad-demo-cases-loading">
        <Spin size="small" />
        <span>正在加载任务...</span>
      </div>
    );
  }

  if (!cases || cases.length === 0) {
    return (
      <div className="tcad-demo-cases">
        <Empty description="当前没有可用任务" />
      </div>
    );
  }

  return (
    <div className="tcad-demo-cases">
      <div className="tcad-demo-cases-header">
        <div className="tcad-demo-cases-title">直接查看自然语言输入、SDE代码生成、电学仿真与紧凑模型构建</div>
        <div className="tcad-demo-cases-subtitle">
          页面提供三组典型任务，便于快速查看结构生成、结果曲线和可导出的工程产物。
        </div>
      </div>

      <div className="tcad-demo-cases-grid">
        {cases.map((item) => (
          <div key={item.case_id} className="tcad-demo-card">
            <div className="tcad-demo-card-title">{item.title || item.case_id}</div>
            <div className="tcad-demo-card-summary">{item.summary}</div>

            <div className="tcad-demo-card-meta">
              <Tag color="blue">{item.device_type || 'tcad'}</Tag>
              <Tag color="geekblue">{item.simulation_type || 'structure'}</Tag>
              {item.profile ? <Tag color="cyan">{item.profile}</Tag> : null}
            </div>

            {item.capabilities?.length ? (
              <div className="tcad-demo-capability-list">
                {item.capabilities.map((capability) => (
                  <span key={capability} className="tcad-demo-capability-chip">
                    {capability}
                  </span>
                ))}
              </div>
            ) : null}

            {item.reference_basis?.length ? (
              <div className="tcad-demo-reference-block">
                <div className="tcad-demo-reference-title">标准依据</div>
                <div className="tcad-demo-reference-list">
                  {item.reference_basis.map((reference) => (
                    <span key={reference} className="tcad-demo-reference-chip">
                      {reference}
                    </span>
                  ))}
                </div>
              </div>
            ) : null}

            {item.artifact_files?.length ? (
              <div className="tcad-demo-artifact-block">
                <div className="tcad-demo-reference-title">展示产物</div>
                <div className="tcad-demo-artifact-list">
                  {item.artifact_files.map((artifact) => (
                    <span key={artifact} className="tcad-demo-artifact-chip">
                      {artifact}
                    </span>
                  ))}
                </div>
              </div>
            ) : null}

            <div className="tcad-demo-card-actions">
              <Button
                type="primary"
                size="large"
                disabled={running}
                onClick={() => onUseCase && onUseCase(item)}
              >
                打开任务
              </Button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};

export default TcadDemoCases;
