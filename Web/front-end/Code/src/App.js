import React, { useEffect, useState } from 'react';
import './App.css';
import Layout from './layout/index';
import { BrowserRouter as Router, Route, Routes, Navigate } from 'react-router-dom';
import Login from './login/login';
import Register from './login/Register';
import Cookies from 'js-cookie';
import { Modal } from 'antd';

function App() {
  const [isAuthenticated, setIsAuthenticated] = useState(() => !!Cookies.get('user'));

  const urlParams = new URLSearchParams(window.location.search);
  const adminBypass = urlParams.get('admin') === 'admin' || localStorage.getItem('adminBypass') === 'true';
  // const [maintenanceMode, setMaintenanceMode] = useState(!adminBypass);
  const [maintenanceMode, setMaintenanceMode] = useState(false);

  useEffect(() => {
    if (urlParams.get('admin') === 'admin') {
      localStorage.setItem('adminBypass', 'true');
      setMaintenanceMode(false);
    }
  }, []);

  useEffect(() => {
    if (maintenanceMode) {
      Modal.info({
        title: '系统维护通知',
        content: '系统维护中，2025/8/26/8:00开放',
        centered: true,
        maskClosable: false,
        closable: false,
        keyboard: false,
      });
    }
  }, [maintenanceMode]);

  // 登录后直接设置认证状态
  const checkAuthentication = () => {
    const cookies = Cookies.get('user');
    setIsAuthenticated(cookies !== undefined);
    // cookie: cookies
  };

  useEffect(() => {
    checkAuthentication();
  }, []);

  // 维护模式下显示维护页面
  if (maintenanceMode) {
    return (
      <div style={{
        position: 'fixed',
        top: 0,
        left: 0,
        width: '100%',
        height: '100%',
        backgroundColor: '#f5f5f5',
        display: 'flex',
        justifyContent: 'center',
        alignItems: 'center',
        zIndex: 9999,
        flexDirection: 'column',
        color: '#666',
        fontSize: '16px',
      }}>
        <div style={{ marginBottom: '10px', fontSize: '18px', fontWeight: 'bold', color: '#1890ff' }}>
          系统维护中
        </div>
        <div>预计2025/8/26/9:30开放</div>
      </div>
    );
  }

  return (
    <Router>
      <div className="App">
        <div className="chat-page-container">
          <Routes>
            {/* 如果没有认证，跳转到登录页面 */}
            <Route path="/main" element={isAuthenticated ? <Layout /> : <Navigate to="/login" />} />
            <Route path="/login" element={<Login checkAuthentication={checkAuthentication} />} />
            <Route path="/register" element={<Register />} />
            <Route path="/" element={isAuthenticated ? <Layout /> : <Navigate to="/login" />} />
            {/* 其他路由 */}
          </Routes>
        </div>
      </div>
    </Router>
  );
}

export default App;