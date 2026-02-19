package com.fabgpt.usermanagement.service;

import com.fabgpt.usermanagement.entity.User;
import com.fabgpt.usermanagement.repository.UserRepository;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.List;
import java.util.Optional;

@Service
public class UserService {

    @Autowired
    private UserRepository userRepository;

    // 用户认证（包含有效期检查）
    public User authenticateUser(String username, String password) {
        Optional<User> userOpt = userRepository.findByUsernameAndPasswordAndStatusActive(username, password);
        if (userOpt.isPresent()) {
            User user = userOpt.get();
            // 更新最后登录时间
            user.setLastLogin(new Date());
            userRepository.save(user);
            return user;
        }
        return null;
    }

    // 获取所有用户
    public List<User> getAllUsers() {
        return userRepository.findAll();
    }

    // 根据ID获取用户
    public User getUserById(Long id) {
        return userRepository.findById(id).orElse(null);
    }

    // 根据用户名获取用户 - 新添加的方法
    public User getUserByUsername(String username) {
        return userRepository.findByUsername(username).orElse(null);
    }

    // 更新用户有效期
    public User updateUserExpire(Long id, String expireDateStr) throws Exception {
        User user = userRepository.findById(id).orElse(null);
        if (user == null) {
            return null;
        }

        // 解析日期字符串
        if (expireDateStr != null && !expireDateStr.trim().isEmpty()) {
            try {
                SimpleDateFormat sdf = new SimpleDateFormat("yyyy-MM-dd HH:mm:ss");
                Date expireDate = sdf.parse(expireDateStr);
                user.setExpireDate(expireDate);
            } catch (Exception e) {
                // 尝试另一种日期格式
                try {
                    SimpleDateFormat sdf = new SimpleDateFormat("yyyy-MM-dd");
                    Date expireDate = sdf.parse(expireDateStr);
                    user.setExpireDate(expireDate);
                } catch (Exception e2) {
                    throw new Exception("日期格式错误，请使用 yyyy-MM-dd 或 yyyy-MM-dd HH:mm:ss 格式");
                }
            }
        } else {
            user.setExpireDate(null); // 清除有效期，表示永久有效
        }

        return userRepository.save(user);
    }

    // 禁用用户
    public User disableUser(Long id) {
        User user = userRepository.findById(id).orElse(null);
        if (user != null) {
            user.setStatus(0); // 设置状态为0（禁用）
            return userRepository.save(user);
        }
        return null;
    }

    // 启用用户
    public User enableUser(Long id) {
        User user = userRepository.findById(id).orElse(null);
        if (user != null) {
            user.setStatus(1); // 设置状态为1（启用）
            return userRepository.save(user);
        }
        return null;
    }
}