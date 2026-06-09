import java.io.*;
import java.net.*;
import java.sql.*;
import java.util.*;
import java.text.SimpleDateFormat;
import java.util.Date;

public class SimpleUserServer {
    private static final String DB_URL = "jdbc:mysql://localhost:4307/zgpt?useSSL=false&serverTimezone=UTC&allowPublicKeyRetrieval=true";
    private static final String DB_USER = "root";
    private static final String DB_PASS = "";
    
    public static void main(String[] args) throws Exception {
        // 加载MySQL驱动
        try {
            Class.forName("com.mysql.cj.jdbc.Driver");
            System.out.println("✅ MySQL驱动加载成功");
        } catch (ClassNotFoundException e) {
            System.out.println("❌ MySQL驱动未找到，请确保mysql-connector-java.jar在classpath中");
            return;
        }
        
        // 测试数据库连接
        try (Connection conn = DriverManager.getConnection(DB_URL, DB_USER, DB_PASS)) {
            System.out.println("✅ 数据库连接成功");
            
            // 测试查询用户
            String sql = "SELECT user_id, username, email, affiliation, position, status, expire_date FROM users LIMIT 5";
            try (PreparedStatement stmt = conn.prepareStatement(sql);
                 ResultSet rs = stmt.executeQuery()) {
                
                System.out.println("📋 用户列表:");
                while (rs.next()) {
                    System.out.printf("ID: %d, 用户名: %s, 邮箱: %s, 状态: %d, 有效期: %s%n",
                        rs.getLong("user_id"),
                        rs.getString("username"),
                        rs.getString("email"),
                        rs.getInt("status"),
                        rs.getTimestamp("expire_date")
                    );
                }
            }
            
        } catch (SQLException e) {
            System.out.println("❌ 数据库连接失败: " + e.getMessage());
            return;
        }
        
        // 启动简单的HTTP服务器
        ServerSocket serverSocket = new ServerSocket(8081);
        System.out.println("🚀 用户管理服务已启动，端口: 8081");
        System.out.println("📋 测试: curl http://localhost:8081/users");
        
        while (true) {
            Socket clientSocket = serverSocket.accept();
            handleRequest(clientSocket);
        }
    }
    
    private static void handleRequest(Socket clientSocket) {
        try (BufferedReader in = new BufferedReader(new InputStreamReader(clientSocket.getInputStream()));
             PrintWriter out = new PrintWriter(clientSocket.getOutputStream(), true)) {
            
            String requestLine = in.readLine();
            System.out.println("📨 收到请求: " + requestLine);
            
            // 简单的路由处理
            if (requestLine != null && requestLine.contains("GET /users")) {
                handleGetUsers(out);
            } else if (requestLine != null && requestLine.contains("GET /")) {
                handleRoot(out);
            } else {
                handle404(out);
            }
            
        } catch (Exception e) {
            e.printStackTrace();
        } finally {
            try {
                clientSocket.close();
            } catch (IOException e) {
                e.printStackTrace();
            }
        }
    }
    
    private static void handleGetUsers(PrintWriter out) {
        try (Connection conn = DriverManager.getConnection(DB_URL, DB_USER, DB_PASS)) {
            String sql = "SELECT user_id, username, email, affiliation, position, status, expire_date, create_time, last_login FROM users";
            
            StringBuilder json = new StringBuilder();
            json.append("[");
            
            try (PreparedStatement stmt = conn.prepareStatement(sql);
                 ResultSet rs = stmt.executeQuery()) {
                
                boolean first = true;
                while (rs.next()) {
                    if (!first) json.append(",");
                    json.append("{");
                    json.append("\"userId\":").append(rs.getLong("user_id")).append(",");
                    json.append("\"username\":\"").append(rs.getString("username")).append("\",");
                    json.append("\"email\":\"").append(rs.getString("email")).append("\",");
                    json.append("\"affiliation\":\"").append(rs.getString("affiliation")).append("\",");
                    json.append("\"position\":\"").append(rs.getString("position")).append("\",");
                    json.append("\"status\":").append(rs.getInt("status")).append(",");
                    
                    Timestamp expireDate = rs.getTimestamp("expire_date");
                    if (expireDate != null) {
                        json.append("\"expireDate\":\"").append(expireDate.toString()).append("\",");
                        json.append("\"isExpired\":").append(expireDate.before(new Date()));
                    } else {
                        json.append("\"expireDate\":null,");
                        json.append("\"isExpired\":false");
                    }
                    
                    json.append("}");
                    first = false;
                }
            }
            
            json.append("]");
            
            // HTTP响应
            out.println("HTTP/1.1 200 OK");
            out.println("Content-Type: application/json; charset=utf-8");
            out.println("Access-Control-Allow-Origin: *");
            out.println("Connection: close");
            out.println();
            out.println(json.toString());
            
        } catch (SQLException e) {
            out.println("HTTP/1.1 500 Internal Server Error");
            out.println("Content-Type: application/json");
            out.println("Connection: close");
            out.println();
            out.println("{\"error\":\"数据库错误: " + e.getMessage() + "\"}");
        }
    }
    
    private static void handleRoot(PrintWriter out) {
        out.println("HTTP/1.1 200 OK");
        out.println("Content-Type: text/html; charset=utf-8");
        out.println("Connection: close");
        out.println();
        out.println("<h1>FabGPT用户管理服务</h1>");
        out.println("<p>服务运行正常</p>");
        out.println("<p>API接口: <a href='/users'>/users</a></p>");
    }
    
    private static void handle404(PrintWriter out) {
        out.println("HTTP/1.1 404 Not Found");
        out.println("Content-Type: text/plain");
        out.println("Connection: close");
        out.println();
        out.println("404 - 页面未找到");
    }
}
