package com.fabgpt.usermanagement.service;

import com.fabgpt.usermanagement.entity.User;
import com.fabgpt.usermanagement.repository.UserRepository;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.text.SimpleDateFormat;
import java.time.Duration;
import java.util.Date;
import java.util.List;
import java.util.Optional;

@Service
public class UserService {

    private static final String DEFAULT_GPTSERVER_LOGIN_URL = "http://127.0.0.1:5107/login";

    @Autowired
    private UserRepository userRepository;

    private final HttpClient httpClient = HttpClient.newBuilder()
            .connectTimeout(Duration.ofSeconds(5))
            .build();

    // 用户认证（包含有效期检查）
    public User authenticateUser(String username, String password) {
        if (username == null || password == null) {
            return null;
        }

        Optional<User> userOpt = userRepository.findByUsername(username);
        if (userOpt.isEmpty()) {
            return null;
        }

        if (!authenticateAgainstGptServer(username, password)) {
            return null;
        }

        User user = userOpt.get();
        user.setLastLogin(new Date());
        return userRepository.save(user);
    }

    private boolean authenticateAgainstGptServer(String username, String password) {
        String loginUrl = System.getenv("WEB_FABGPT_GPTSERVER_LOGIN_URL");
        if (loginUrl == null || loginUrl.trim().isEmpty()) {
            loginUrl = DEFAULT_GPTSERVER_LOGIN_URL;
        }

        String payload = String.format(
                "{\"username\":\"%s\",\"password\":\"%s\"}",
                escapeJson(username),
                escapeJson(password)
        );

        HttpRequest request = HttpRequest.newBuilder()
                .uri(URI.create(loginUrl))
                .header("Content-Type", "application/json")
                .timeout(Duration.ofSeconds(10))
                .POST(HttpRequest.BodyPublishers.ofString(payload, StandardCharsets.UTF_8))
                .build();

        try {
            HttpResponse<String> response = httpClient.send(
                    request,
                    HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8)
            );
            return response.statusCode() == 200;
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return false;
        } catch (Exception e) {
            return false;
        }
    }

    private String escapeJson(String value) {
        return value
                .replace("\\", "\\\\")
                .replace("\"", "\\\"");
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

        if (expireDateStr != null && !expireDateStr.trim().isEmpty()) {
            try {
                SimpleDateFormat sdf = new SimpleDateFormat("yyyy-MM-dd HH:mm:ss");
                Date expireDate = sdf.parse(expireDateStr);
                user.setExpireDate(expireDate);
            } catch (Exception e) {
                try {
                    SimpleDateFormat sdf = new SimpleDateFormat("yyyy-MM-dd");
                    Date expireDate = sdf.parse(expireDateStr);
                    user.setExpireDate(expireDate);
                } catch (Exception e2) {
                    throw new Exception("日期格式错误，请使用 yyyy-MM-dd 或 yyyy-MM-dd HH:mm:ss 格式");
                }
            }
        } else {
            user.setExpireDate(null);
        }

        return userRepository.save(user);
    }

    // 禁用用户
    public User disableUser(Long id) {
        User user = userRepository.findById(id).orElse(null);
        if (user != null) {
            user.setStatus(0);
            return userRepository.save(user);
        }
        return null;
    }

    // 启用用户
    public User enableUser(Long id) {
        User user = userRepository.findById(id).orElse(null);
        if (user != null) {
            user.setStatus(1);
            return userRepository.save(user);
        }
        return null;
    }
}
