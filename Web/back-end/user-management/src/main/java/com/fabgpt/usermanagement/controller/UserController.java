package com.fabgpt.usermanagement.controller;

import com.fabgpt.usermanagement.entity.User;
import com.fabgpt.usermanagement.service.UserService;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/api")
@CrossOrigin(origins = "*")
public class UserController {

    @Autowired
    private UserService userService;

    // 用户登录 - 带有效期检查
    @PostMapping("/login")
    public ResponseEntity<?> login(@RequestBody Map<String, String> loginRequest) {
        String username = loginRequest.get("username");
        String password = loginRequest.get("password");

        try {
            User user = userService.authenticateUser(username, password);
            if (user != null) {
                if (user.isValid()) {
                    Map<String, Object> response = new HashMap<>();
                    response.put("message", "登录成功");
                    response.put("username", user.getUsername());
                    response.put("expireDate", user.getExpireDate() != null ? user.getExpireDate().toString() : "永久有效");
                    return ResponseEntity.ok().body(response);
                } else if (user.isExpired()) {
                    Map<String, Object> response = new HashMap<>();
                    response.put("message", "账户已过期，请联系管理员续期");
                    return ResponseEntity.status(401).body(response);
                } else {
                    Map<String, Object> response = new HashMap<>();
                    response.put("message", "账户已被禁用，请联系管理员");
                    return ResponseEntity.status(401).body(response);
                }
            } else {
                Map<String, Object> response = new HashMap<>();
                response.put("message", "用户名或密码错误");
                return ResponseEntity.status(401).body(response);
            }
        } catch (Exception e) {
            Map<String, Object> response = new HashMap<>();
            response.put("message", "登录过程中发生错误");
            return ResponseEntity.status(500).body(response);
        }
    }

    // 获取所有用户信息 - 显示真实密码
    @GetMapping("/users")
    public ResponseEntity<List<User>> getAllUsers() {
        try {
            List<User> users = userService.getAllUsers();
            // 注释掉密码隐藏，显示真实密码
            // users.forEach(user -> user.setPassword("***"));
            return ResponseEntity.ok(users);
        } catch (Exception e) {
            return ResponseEntity.status(500).build();
        }
    }

    // 获取所有用户信息 - 管理员专用（显示密码）
    @GetMapping("/admin/users")
    public ResponseEntity<List<User>> getAllUsersWithPassword() {
        try {
            List<User> users = userService.getAllUsers();
            // 管理员接口，显示真实密码
            return ResponseEntity.ok(users);
        } catch (Exception e) {
            return ResponseEntity.status(500).build();
        }
    }

    // 获取用户状态 - 新添加的API
    @GetMapping("/users/{username}/status")
    public ResponseEntity<?> getUserStatus(@PathVariable String username) {
        try {
            User user = userService.getUserByUsername(username);
            if (user != null) {
                Map<String, Object> response = new HashMap<>();
                response.put("status", user.getStatus());
                response.put("expireDate", user.getExpireDate());
                response.put("expired", user.isExpired());
                response.put("valid", user.isValid());
                return ResponseEntity.ok().body(response);
            } else {
                return ResponseEntity.notFound().build();
            }
        } catch (Exception e) {
            // 添加详细的错误信息记录
            e.printStackTrace();
            String errorMessage = e.getMessage() != null ? e.getMessage() : e.getClass().getSimpleName();
            Map<String, Object> response = new HashMap<>();
            response.put("message", "获取用户状态失败：" + errorMessage);
            response.put("error", e.getClass().getSimpleName());
            return ResponseEntity.status(500).body(response);
        }
    }

    // 更新用户有效期
    @PutMapping("/users/{id}/expire")
    public ResponseEntity<?> updateUserExpire(@PathVariable Long id,
                                             @RequestBody Map<String, String> request) {
        try {
            String expireDateStr = request.get("expireDate");
            User updatedUser = userService.updateUserExpire(id, expireDateStr);
            if (updatedUser != null) {
                Map<String, Object> response = new HashMap<>();
                response.put("message", "用户有效期更新成功");
                response.put("user", updatedUser.getUsername());
                response.put("expireDate", updatedUser.getExpireDate() != null ?
                             updatedUser.getExpireDate().toString() : "永久有效");
                return ResponseEntity.ok().body(response);
            } else {
                return ResponseEntity.notFound().build();
            }
        } catch (Exception e) {
            Map<String, Object> response = new HashMap<>();
            response.put("message", "更新失败：" + e.getMessage());
            return ResponseEntity.status(500).body(response);
        }
    }

    // 禁用用户
    @PutMapping("/users/{id}/disable")
    public ResponseEntity<?> disableUser(@PathVariable Long id) {
        try {
            User user = userService.disableUser(id);
            if (user != null) {
                Map<String, Object> response = new HashMap<>();
                response.put("message", "用户已禁用");
                response.put("username", user.getUsername());
                return ResponseEntity.ok().body(response);
            } else {
                return ResponseEntity.notFound().build();
            }
        } catch (Exception e) {
            Map<String, Object> response = new HashMap<>();
            response.put("message", "操作失败：" + e.getMessage());
            return ResponseEntity.status(500).body(response);
        }
    }

    // 启用用户
    @PutMapping("/users/{id}/enable")
    public ResponseEntity<?> enableUser(@PathVariable Long id) {
        try {
            User user = userService.enableUser(id);
            if (user != null) {
                Map<String, Object> response = new HashMap<>();
                response.put("message", "用户已启用");
                response.put("username", user.getUsername());
                return ResponseEntity.ok().body(response);
            } else {
                return ResponseEntity.notFound().build();
            }
        } catch (Exception e) {
            Map<String, Object> response = new HashMap<>();
            response.put("message", "操作失败：" + e.getMessage());
            return ResponseEntity.status(500).body(response);
        }
    }

    // 获取单个用户信息
    @GetMapping("/users/{id}")
    public ResponseEntity<User> getUserById(@PathVariable Long id) {
        try {
            User user = userService.getUserById(id);
            if (user != null) {
                user.setPassword("***"); // 单个用户查询时隐藏密码
                return ResponseEntity.ok(user);
            } else {
                return ResponseEntity.notFound().build();
            }
        } catch (Exception e) {
            return ResponseEntity.status(500).build();
        }
    }
}