import React, { useState } from "react";
import "./login.css";
import { useNavigate } from "react-router-dom";
import { useDispatch } from 'react-redux';
import Cookies from 'js-cookie';
import { updateUsername } from '../store/userStore'; 

function Login({ checkAuthentication }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();
  const dispatch = useDispatch();

  // 获取用户状态数据 - 使用单独的状态检查API
  const getUserStatus = async (username) => {
    try {
      // 使用专门的状态检查接口，避免获取完整用户数据
      const response = await fetch(`http://10.98.64.22:5203/api/users/${username}/status`, {
        method: "GET",
        headers: {
          "Content-Type": "application/json",
        },
      });

      if (response.ok) {
        const statusData = await response.json();
        return {
          status: statusData.status,
          expireDate: statusData.expireDate,
          expired: statusData.expired
        };
      } else if (response.status === 404) {
        // 用户不存在，让密码验证来处理
        return null;
      } else {
        return null;
      }
    } catch (error) {
      // 网络错误，允许继续登录流程
      return null;
    }
  };

  // 检查用户状态的函数
  const checkUserStatus = (userData) => {
    if (!userData) {
      return { isValid: true };
    }

    const { status, expired, expireDate } = userData;
    
    // 检查用户是否被禁用
    if (status === 0) {
      return {
        isValid: false,
        message: "您的账户已被禁用，请联系管理员"
      };
    }
    
    // 检查用户是否过期
    if (expired === true) {
      return {
        isValid: false,
        message: `您的账户已过期${expireDate ? `（过期时间：${new Date(expireDate).toLocaleString()}）` : ''}，请联系管理员续期`
      };
    }

    // 如果有过期时间，进行额外检查
    if (expireDate) {
      const expireTime = new Date(expireDate).getTime();
      const currentTime = new Date().getTime();
      
      // 检查是否已经过期
      if (expireTime < currentTime) {
        return {
          isValid: false,
          message: `您的账户已过期（过期时间：${new Date(expireDate).toLocaleString()}），请联系管理员续期`
        };
      }
      
      // 检查是否即将过期（7天内）
      const daysLeft = Math.ceil((expireTime - currentTime) / (1000 * 60 * 60 * 24));
      
      if (daysLeft <= 7 && daysLeft > 0) {
        return {
          isValid: true,
          warning: `您的账户将在 ${daysLeft} 天后过期（${new Date(expireDate).toLocaleString()}），请及时联系管理员续期`
        };
      }
    }
    
    return { isValid: true };
  };

  const handleLogin = async () => {
    if (!username || !password) {
      window.alert("请输入用户名和密码");
      return;
    }

    setLoading(true);

    try {
      // 同时进行状态检查和密码验证，避免多次网络请求
      const [userStatus, loginResponse] = await Promise.all([
        getUserStatus(username),
        fetch("http://10.98.64.22:8080/login", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ username, password }),
        })
      ]);

      // 先检查密码是否正确
      if (!loginResponse.ok) {
        window.alert("用户名或密码错误");
        return;
      }

      // 密码正确后再检查用户状态
      const statusCheck = checkUserStatus(userStatus);
      
      if (!statusCheck.isValid) {
        window.alert(statusCheck.message);
        return;
      }

      // 如果有警告信息（如即将过期），显示提醒
      if (statusCheck.warning) {
        const continueLogin = window.confirm(
          `${statusCheck.warning}\n\n是否继续登录？`
        );
        if (!continueLogin) {
          return;
        }
      }

      // 登录成功，设置Cookie并跳转
      Cookies.set('user', username, { expires: 7 });
      dispatch(updateUsername(username));
      checkAuthentication();
      navigate("/main");
        
    } catch (error) {
      window.alert("网络连接错误，请稍后重试");
    } finally {
      setLoading(false);
    }
  };

  const handleRegister = () => {
    window.open("https://jsj.top/f/YPTj9b", "_blank");
  };

  // 处理回车键登录
  const handleKeyPress = (e) => {
    if (e.key === 'Enter' && !loading) {
      handleLogin();
    }
  };

  return (
    <div className="login-container">
      <h2 className="login-title">欢迎您使用FabGPT</h2>
      <div className="login-form">
        <input
          type="text"
          placeholder="用户名"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          onKeyPress={handleKeyPress}
          disabled={loading}
          autoComplete="username"
        />
        <input
          type="password"
          placeholder="密码"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          onKeyPress={handleKeyPress}
          disabled={loading}
          autoComplete="current-password"
        />
        <div className="button-container">
          <button 
            className="login-button" 
            onClick={handleLogin}
            disabled={loading}
            type="button"
          >
            {loading ? "登录中..." : "登录"}
          </button>
          <button 
            className="register-button" 
            onClick={handleRegister}
            disabled={loading}
            type="button"
          >
            注册
          </button>
        </div>
      </div>
    </div>
  );
}

export default Login;