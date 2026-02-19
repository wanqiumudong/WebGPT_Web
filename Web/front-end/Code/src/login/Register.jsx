import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import "./register.css";
// 如果不再使用 Redux，可以移除下面的 import 语句
// import { useDispatch, useSelector } from "react-redux";

function Register() {
  const navigate = useNavigate();
  const [newUsername, setNewUsername] = useState("");
  const [newName, setNewName] = useState("");
  const [newWorkplace, setNewWorkplace] = useState("");
  const [newEmail, setNewEmail] = useState("");
  const [errorMessage, setErrorMessage] = useState("");

  const handleRegister = async () => {
    window.alert("您已经注册成功,我们的工作人员会与您联系");
    navigate("/login");
    // try {
    //   // 验证用户名格式
    //   if (!isValidUsername(newUsername)) {
    //     setErrorMessage("用户名必须至少为 6 个字符，由英文字母和数字组成");
    //     return;
    //   }

    //   // 验证邮箱格式
    //   if (!isValidEmail(newEmail)) {
    //     setErrorMessage("请输入有效的电子邮箱地址");
    //     return;
    //   }

    //   // 发送注册请求到后端
    //   const response = await fetch("http://localhost:2223/register", {
    //     method: "POST",
    //     headers: {
    //       "Content-Type": "application/json",
    //     },
    //     body: JSON.stringify({
    //       username: newUsername,
    //       name: newName,
    //       workplace: newWorkplace,
    //       email: newEmail,
    //     }),
    //   });

    //   if (response.ok) {
    //     // 注册成功
    //     window.alert("您已经注册成功,我们的工作人员会与您联系");
    //     navigate("/login");
    //   } else {
    //     // 注册失败，这里可能需要根据后端返回的具体错误信息进行更详细的提示
    //     const responseBody = await response.json();
    //     setErrorMessage(responseBody.message || "注册失败，请稍后再试");
    //   }
    // } catch (error) {
    //   console.error("Error during registration:", error);
    //   setErrorMessage("注册过程中发生错误，请稍后再试");
    // }
  };

  // 验证用户名格式
  const isValidUsername = (username) => {
    return /^[A-Za-z0-9]{6,}$/.test(username);
  };

  // 验证邮箱格式
  const isValidEmail = (email) => {
    // 使用简单的正则表达式验证邮箱格式
    return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
  };

  return (
    <div className="register-container">
      <h2>注册新用户</h2>
      <input
        type="text"
        placeholder="用户名(至少为 6 个字符，由英文字母和数字组成)"
        value={newUsername}
        onChange={(e) => setNewUsername(e.target.value)}
      />
      <input
        type="text"
        placeholder="姓名"
        value={newName}
        onChange={(e) => setNewName(e.target.value)}
      />
      <input
        type="text"
        placeholder="工作单位"
        value={newWorkplace}
        onChange={(e) => setNewWorkplace(e.target.value)}
      />
      <input
        type="email"
        placeholder="电子邮箱"
        value={newEmail}
        onChange={(e) => setNewEmail(e.target.value)}
      />
      <button onClick={handleRegister}>注册</button>
      {errorMessage && <p className="error-message">{errorMessage}</p>}
    </div>
  );
}

export default Register;