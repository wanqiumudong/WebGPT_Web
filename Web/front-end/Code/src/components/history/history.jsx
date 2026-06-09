import React, { useEffect, useState } from "react";
import styled from "styled-components";
import { Input, Modal, message } from "antd";
import { DeleteOutlined, EditOutlined, MoreOutlined } from "@ant-design/icons";
import { useSelector } from "react-redux";
import { addSession } from "./api";
import { getHistorySession } from "./api";
import { format } from "date-fns";
import Cookies from "js-cookie";
import { BACKEND_BASE_URL, TCAD_BASE_URL } from "../../config/endpoints";

const Container = styled.div`
  width: 240px;
  height: 100%;
  min-height: 0;
  margin-left: 40px;
  box-shadow: 1px 4px 12px 0px rgba(0, 0, 0, 0.2);
  padding: 16px;
  background-color: #fafafa;
  display: flex;
  flex-direction: column;
  box-sizing: border-box;
  overflow: hidden;
  flex-shrink: 0;
`;

const ScrollableArea = styled.div`
  flex: 1;
  min-height: 0;
  overflow-y: auto; /* 添加垂直滚动条 */
  padding-right: 8px; /* 为滚动条留出空间 */
`;

const Item = styled.div`
  background-color: #ffffff;
  border: 1px solid #f0f0f0;
  display: flex;
  padding: 14px 20px;
  position: relative;
  justify-content: space-between;
  border-radius: 8px;
  margin-bottom: 6px;
  cursor: pointer;
  transition: all 0.15s ease;
  
  &:hover {
    background-color: #fafafa;
    border-color: #e8e8e8;
  }
`;

const Title = styled.div`
  font-size: 15px;
  line-height: 20px;
  padding-right: 16px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  color: #262626;
  font-weight: 500;
`;

const ButtonContainer = styled.div`
  margin-top: 20px;
  display: flex;
  justify-content: flex-start;
  margin-bottom: 20px;
`;

const HistoryTitle = styled.div`
  font-size: 14px;
  color: #8c8c8c;
  margin-bottom: 16px;
  margin-left: 5px;
  font-weight: 600;
  letter-spacing: 0.3px;
  text-transform: uppercase;
`;

const NewSessionButton = styled.div`
  background-color: #fafafa;
  border: 1px solid #e8e8e8;
  display: flex;
  padding: 14px 20px;
  position: relative;
  justify-content: flex-start;
  align-items: center;
  border-radius: 8px;
  margin-bottom: 8px;
  cursor: pointer;
  width: 100%;
  gap: 12px;
  transition: all 0.15s ease;
  
  &:hover {
    background-color: #f0f0f0;
    border-color: #d0d0d0;
  }
  
  .plus-icon {
    width: 20px;
    height: 20px;
    border-radius: 4px;
    display: flex;
    align-items: center;
    justify-content: center;
    background: #f0f0f0;
    border: 1px solid #d9d9d9;
    position: relative;
    transition: all 0.15s ease;
    
    &::before {
      content: '';
      position: absolute;
      width: 10px;
      height: 2px;
      background: #595959;
      border-radius: 1px;
    }
    
    &::after {
      content: '';
      position: absolute;
      width: 2px;
      height: 10px;
      background: #595959;
      border-radius: 1px;
    }
  }
  
  &:hover .plus-icon {
    background: #e6e6e6;
    border-color: #bfbfbf;
  }
  
  .button-text {
    font-size: 15px;
    line-height: 20px;
    color: #262626;
    font-weight: 500;
  }
`;

const ActionContainer = styled.div`
  position: relative;
  display: flex;
  align-items: center;
`;

const MoreButton = styled.div`
  width: 20px;
  height: 20px;
  display: flex;
  justify-content: center;
  align-items: center;
  cursor: pointer;
  border-radius: 4px;
  opacity: 0;
  transition: opacity 0.2s;
  
  ${Item}:hover & {
    opacity: 1;
  }
  
  &:hover {
    background-color: rgba(0, 0, 0, 0.1);
  }
`;

const ActionMenu = styled.div`
  position: absolute;
  top: 100%;
  right: 0;
  background: white;
  border: 1px solid #e8e8e8;
  border-radius: 6px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
  z-index: 1000;
  min-width: 120px;
  padding: 6px 0;
  white-space: nowrap;
`;

const ActionMenuItem = styled.div`
  padding: 10px 16px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  font-size: 14px;
  color: #595959;
  transition: all 0.15s ease;
  font-weight: 500;
  
  &:hover {
    background-color: #fafafa;
    color: #262626;
  }
  
  &.danger {
    &:hover {
      background-color: #fff1f0;
      color: #cf1322;
    }
  }
  
  &:first-child {
    border-radius: 4px 4px 0 0;
  }
  
  &:last-child {
    border-radius: 0 0 4px 4px;
  }
  
  &:only-child {
    border-radius: 4px;
  }
  
  .anticon {
    font-size: 14px;
    width: 14px;
    height: 14px;
    display: flex;
    align-items: center;
    justify-content: center;
  }
`;

const RenameInput = styled(Input)`
  font-size: 15px;
  border: 1px solid #d9d9d9;
  border-radius: 4px;
  padding: 2px 8px;
`;

const sleep = ms => new Promise(
  resolve => setTimeout(resolve, ms)
);

export const History = ({ modelId }) => {
  const mainPage = useSelector((state) => state.PageState.Main_Page);
  const subPage = useSelector((state) => state.PageState.Sub_Page);

  const [data, setData] = useState([]);
  const [error, setError] = useState(null);
  const [isDataLoaded, setIsDataLoaded] = useState(false); // 新增状态，用于判断是否完成数据加载
  const [activeMenuSessionId, setActiveMenuSessionId] = useState(null); // 当前显示操作菜单的会话ID
  const [editingSessionId, setEditingSessionId] = useState(null); // 当前正在编辑的会话ID
  const [editingTitle, setEditingTitle] = useState(''); // 编辑中的标题

  const userCookie = Cookies.get("user");

  async function fetchData(modelId) {
    try {
      const response = await fetch(
        `${BACKEND_BASE_URL}/session/get-by-user-model?username=${userCookie}&modelId=${modelId}`,
        {
          method: "GET",
        }
      );
      if (response.ok) {
        const data = await response.json();
        setData(data || []);
        setIsDataLoaded(true); // 数据加载完成
      }
    } catch (err) {
      setError(err);
    }
  }

  async function fetchData_chat(modelId) {
    try {
      const response = await fetch(
        `${BACKEND_BASE_URL}/session/get-by-user-model?username=${userCookie}&modelId=${modelId}`,
        {
          method: "GET",
        }
      );
      if (response.ok) {
        const data = await response.json();
        setData(data || []);
      }
    } catch (err) {
      setError(err);
    }
  }

  useEffect(() => {
    const handleSessionUpdate = () => {
      fetchData_chat(modelId);
    };

    window.addEventListener("sessionUpdated", handleSessionUpdate);

    const timer = setTimeout(() => {
    fetchData(modelId); // 初始化加载
    
    // 检查当前是否有有效的sessionId，如果没有则设为默认状态
    let currentSessionId;
    if (modelId == 5) {
      currentSessionId = Cookies.get('circuit_5');
    } else if (modelId == 0) {
      currentSessionId = Cookies.get(5);
    } else {
      currentSessionId = Cookies.get(modelId);
    }
    
    if (!currentSessionId) {
      handleNewSession();
    }
    }, 10);

    return () => {
      window.removeEventListener("sessionUpdated", handleSessionUpdate);
      clearTimeout(timer);
    };
  }, [modelId]);

  // 移除自动创建会话的逻辑
  // useEffect(() => {
  //   const timer = setTimeout(() => {
  //     if (isDataLoaded) {
  //       fetchUserIdAndAddSession();
  //       setIsDataLoaded(false);
  //     }
  //   }, 0);
  //   return () => clearTimeout(timer);
  // }, [isDataLoaded]);

  // 定义默认会话标识符
  const DEFAULT_SESSION = 'DEFAULT_SESSION';

  const handleItemClick = (sessionId) => {
    if (modelId == 5) {
      // CircuitThink使用特殊的cookie key
      Cookies.set('circuit_5', sessionId, { expires: 7 });
    } else if (modelId == 0) {
      Cookies.set(5, sessionId, { expires: 7 });
    } else {
      Cookies.set(modelId, sessionId, { expires: 7 });
    }
  };

  // 新会话处理 - 进入默认对话状态
  const handleNewSession = () => {
    if (modelId == 5) {
      Cookies.set('circuit_5', DEFAULT_SESSION, { expires: 7 });
    } else if (modelId == 0) {
      Cookies.set(5, DEFAULT_SESSION, { expires: 7 });
    } else {
      Cookies.set(modelId, DEFAULT_SESSION, { expires: 7 });
    }
  };

  async function checkSessionContent(sessionId) {
    try {
      const response = await fetch(
        `${BACKEND_BASE_URL}/message/list-by-session?sessionId=${sessionId}`
      );
      if (response.ok) {
        const messages = await response.json();
        return messages.length > 0;
      }
    } catch (err) {
      setError(err);
    }
    return false;
  }

  // 当产生实际对话时创建真实的会话记录
  async function createRealSessionAfterChat() {
    try {
      const response = await fetch(
        `${BACKEND_BASE_URL}/user/get-by-name?username=${userCookie}`
      );
      if (response.ok) {
        const userData = await response.json();
        const timestamp = format(new Date(), "yyyy-MM-dd'T'HH:mm:ss.SSSxxx");
        const timestamp_short = format(new Date(), "yyMMddHHmmss");

        const newSession = {
          createTime: timestamp,
          header: "新会话",
          lastActive: timestamp,
          modelId: modelId,
          sessionId: timestamp_short,
          status: 1,
          userId: userData.userId,
        };

        const addResponse = await addSession(newSession);
        const addOk = !!(
          addResponse &&
          (addResponse.success === true || addResponse.sessionId || addResponse.session_id)
        );
        if (addOk) {
          // 更新cookie为真实的sessionId
          if (modelId == 5) {
            Cookies.set('circuit_5', newSession.sessionId, { expires: 7 });
          } else if (modelId == 0) {
            Cookies.set(5, newSession.sessionId, { expires: 7 });
          } else {
            Cookies.set(modelId, newSession.sessionId, { expires: 7 });
          }

          // 添加到历史记录
          setData((prevData) => [newSession, ...prevData]);
          
          return newSession.sessionId;
        }
      }
    } catch (err) {
      setError(err);
      return null;
    }
  }

  // 保留原有的fetchUserIdAndAddSession函数以防其他地方调用
  async function fetchUserIdAndAddSession() {
    handleNewSession();
  }

  const deleteSession = async (sessionId) => {
    try {
      if (modelId === 3) {
        const cleanupResponse = await fetch(`${TCAD_BASE_URL}/delete_session_runtime`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            user_id: userCookie,
            conversation_id: sessionId,
          }),
        });
        if (!cleanupResponse.ok) {
          throw new Error("Failed to cleanup TCAD session runtime");
        }
      }
      const response_1 = await fetch(
        `${BACKEND_BASE_URL}/message/delete-by-session?sessionId=${sessionId}`,
        {
          method: "GET",
        }
      );
      const response_2 = await fetch(
        `${BACKEND_BASE_URL}/session/delete?sessionId=${sessionId}`,
        {
          method: "GET",
        }
      );
      if (response_1.ok && response_2.ok) {
        // 获取当前活跃的sessionId
        let currentSessionId;
        if (modelId == 5) {
          currentSessionId = Cookies.get('circuit_5');
        } else if (modelId == 0) {
          currentSessionId = Cookies.get(5);
        } else {
          currentSessionId = Cookies.get(modelId);
        }

        // 从历史记录中移除被删除的会话
        setData((prevData) =>
          prevData.filter((session) => session.sessionId !== sessionId)
        );
        setActiveMenuSessionId(null); // 关闭菜单

        // 如果删除的是当前会话，切换到默认状态
        if (currentSessionId === sessionId) {
          handleNewSession();
          
          // 🔧 延迟触发页面状态重置事件，确保Cookie已经设置
          setTimeout(() => {
            window.dispatchEvent(new CustomEvent('sessionDeleted', { 
              detail: { 
                deletedSessionId: sessionId, 
                modelId: modelId,
                shouldResetToDefault: true
              } 
            }));
          }, 50);
        }
      } else {
        throw new Error("Failed to delete session");
      }
    } catch (err) {
      setError(err);
      message.error("删除对话失败，请稍后重试");
    }
  };

  const confirmDeleteSession = (sessionId, header) => {
    const title = modelId === 3 ? '确认删除TCAD对话' : '确认删除对话';
    const content = modelId === 3
      ? `确定要删除对话“${header}”吗？对应历史记录和TCAD会话工作区文件将一并清理，此操作不可恢复。`
      : `确定要删除对话“${header}”吗？此操作不可恢复。`;
    Modal.confirm({
      title,
      content,
      okText: '删除',
      okType: 'danger',
      cancelText: '取消',
      onOk: () => deleteSession(sessionId),
    });
  };

  // 重命名会话
  const renameSession = async (sessionId, newTitle) => {
    try {
      // 限制标题长度为8个字符
      const truncatedTitle = newTitle.slice(0, 8);
      
      // 获取会话信息
      const session = data.find(item => item.sessionId === sessionId);
      if (!session) return;

      // 更新会话标题
      const updatedSession = {
        ...session,
        header: truncatedTitle
      };

      const response = await fetch(`${BACKEND_BASE_URL}/session/update`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(updatedSession),
      });

      if (response.ok) {
        setData((prevData) =>
          prevData.map((session) =>
            session.sessionId === sessionId
              ? { ...session, header: truncatedTitle }
              : session
          )
        );
      } else {
        throw new Error("Failed to rename session");
      }
    } catch (err) {
      setError(err);
    }
  };

  // 处理点击外部关闭菜单
  useEffect(() => {
    const handleClickOutside = (event) => {
      if (activeMenuSessionId && !event.target.closest('.action-menu')) {
        setActiveMenuSessionId(null);
      }
    };

    document.addEventListener('click', handleClickOutside);
    return () => {
      document.removeEventListener('click', handleClickOutside);
    };
  }, [activeMenuSessionId]);

  // 处理重命名
  const handleRenameStart = (sessionId, currentTitle) => {
    setEditingSessionId(sessionId);
    setEditingTitle(currentTitle);
    setActiveMenuSessionId(null);
  };

  const handleRenameSubmit = (sessionId) => {
    if (editingTitle.trim()) {
      renameSession(sessionId, editingTitle.trim());
    }
    setEditingSessionId(null);
    setEditingTitle('');
  };

  const handleRenameCancel = () => {
    setEditingSessionId(null);
    setEditingTitle('');
  };

  if (error) {
    return <div>Error: {error.message}</div>;
  }

  return (
    <Container>
      <ButtonContainer>
        <NewSessionButton onClick={handleNewSession}>
          <div className="plus-icon"></div>
          <span className="button-text">新会话</span>
        </NewSessionButton>
      </ButtonContainer>
      <HistoryTitle>对话历史</HistoryTitle>
      <ScrollableArea>
        {data.length > 0 ? (
          data
            .sort((a, b) => new Date(b.lastActive) - new Date(a.lastActive))
            .filter((item, index, array) => {
              const seen = new Set();
              const uniqueData = array
                .filter((subItem) => {
                  if (seen.has(subItem.sessionId)) {
                    return false;
                  }
                  seen.add(subItem.sessionId);
                  return true;
                });
              return uniqueData.includes(item);
            })
            .map((item) => (
              <Item
                key={item.sessionId}
                onClick={(e) => {
                  // 如果点击的是操作按钮区域，不触发选择会话
                  if (e.target.closest('.action-container')) {
                    e.stopPropagation();
                    return;
                  }
                  handleItemClick(item.sessionId);
                }}
              >
                {editingSessionId === item.sessionId ? (
                  <RenameInput
                    value={editingTitle}
                    onChange={(e) => setEditingTitle(e.target.value)}
                    onPressEnter={() => handleRenameSubmit(item.sessionId)}
                    onBlur={handleRenameCancel}
                    autoFocus
                    maxLength={8}
                    placeholder="输入新标题(最多8字符)"
                  />
                ) : (
                  <Title>{item.header}</Title>
                )}
                
                <ActionContainer className="action-container">
                  <MoreButton
                    onClick={(e) => {
                      e.stopPropagation();
                      setActiveMenuSessionId(
                        activeMenuSessionId === item.sessionId ? null : item.sessionId
                      );
                    }}
                  >
                    <MoreOutlined />
                  </MoreButton>
                  
                  {activeMenuSessionId === item.sessionId && (
                    <ActionMenu className="action-menu">
                      <ActionMenuItem
                        onClick={(e) => {
                          e.stopPropagation();
                          handleRenameStart(item.sessionId, item.header);
                        }}
                      >
                        <EditOutlined />
                        重命名
                      </ActionMenuItem>
                      <ActionMenuItem
                        className="danger"
                        onClick={(e) => {
                          e.stopPropagation();
                          confirmDeleteSession(item.sessionId, item.header);
                        }}
                      >
                        <DeleteOutlined />
                        删除
                      </ActionMenuItem>
                    </ActionMenu>
                  )}
                </ActionContainer>
              </Item>
            ))
        ) : (
          <div>暂无会话</div>
        )}
      </ScrollableArea>
    </Container>
  );
};

// 导出工具函数供其他组件使用
export const DEFAULT_SESSION = 'DEFAULT_SESSION';

// 辅助函数：在产生对话后创建真实会话
export const createRealSessionAfterChat = async (modelId) => {
  const userCookie = Cookies.get("user");
  
  try {
    const response = await fetch(
      `${BACKEND_BASE_URL}/user/get-by-name?username=${userCookie}`
    );
    if (response.ok) {
      const userData = await response.json();
      const timestamp = format(new Date(), "yyyy-MM-dd'T'HH:mm:ss.SSSxxx");
      const timestamp_short = format(new Date(), "yyMMddHHmmss");

      const newSession = {
        createTime: timestamp,
        header: "新会话",
        lastActive: timestamp,
        modelId: modelId,
        sessionId: timestamp_short,
        status: 1,
        userId: userData.userId,
      };

      const addResponse = await addSession(newSession);
      const addOk = !!(
        addResponse &&
        (addResponse.success === true || addResponse.sessionId || addResponse.session_id)
      );
      if (addOk) {
        // 更新cookie为真实的sessionId
        if (modelId == 5) {
          Cookies.set('circuit_5', newSession.sessionId, { expires: 7 });
        } else if (modelId == 0) {
          Cookies.set(5, newSession.sessionId, { expires: 7 });
        } else {
          Cookies.set(modelId, newSession.sessionId, { expires: 7 });
        }

        // 🔧 触发历史更新事件
        window.sessionUpdated = Date.now();
        window.dispatchEvent(new Event("sessionUpdated"));

        return newSession.sessionId;
      }
    }
  } catch (err) {
    return null;
  }
  return null;
};

// 辅助函数：检查是否为默认会话状态
export const isDefaultSession = (modelId) => {
  let currentSessionId;
  if (modelId == 5) {
    currentSessionId = Cookies.get('circuit_5');
  } else if (modelId == 0) {
    currentSessionId = Cookies.get(5);
  } else {
    currentSessionId = Cookies.get(modelId);
  }
  return currentSessionId === DEFAULT_SESSION;
};
