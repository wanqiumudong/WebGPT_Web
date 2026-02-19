# Web-FabGPT 完整部署规划 - 统一8路负载均衡架构

## 🎯 架构总览

### 系统分工
- **本地服务器 (10.98.64.22)**: 前端Web + 用户管理 + 轻量级AI服务 + 智能负载均衡器
- **远程GPU服务器 (10.98.193.46)**: GPU密集型AI推理服务 (8张GPU卡并行)

### 统一智能负载均衡设计
1. **5个智能负载均衡器**: 每个AI服务都有专用负载均衡器
2. **8路并行处理**: 每个AI服务部署8个实例，最大化GPU利用
3. **智能路由算法**: 基于响应时间、请求负载、健康状态自动选择最优实例
4. **故障自动恢复**: 实例健康检查和自动切换
5. **用户会话隔离**: 每个用户独立的数据目录和对话历史

---

## 📋 详细部署清单

### 🔹 本地服务器 (10.98.64.22) 部署

#### 1. 基础Web服务

**前端React服务**
```bash
cd /data/Web-FabGPT/Web/front-end/Code/
npm install
npm start  # 端口: 3000
```

**gptserver服务**
```bash
cd /data/Web-FabGPT/Web/back-end/gptserver/bin
./start.sh  # 端口: 8080

**Java用户管理服务**
```bash
cd /data/Web-FabGPT/Web/back-end/user-management/
./build.sh
./start.sh  # 端口: 5203
```

**MySQL数据库服务**
```bash
cd /data/Web-FabGPT/Web/back-end/mysql/
./start.sh  # 端口: 4306
```

#### 2. 本地AI服务集群 (8实例并行)

**TCAD技术文档RAG服务** (本地CPU，无GPU需求)
```bash  
cd /data/Web-FabGPT/LLM/TCAD_code/
# 8个TCAD实例，端口5002,5012,5022,5032,5042,5052,5062,5072
for i in {0..7}; do
  port=$((5002 + i * 10))
  INSTANCE_ID=$i SERVICE_PORT=$port python TCAD_web_MilvusRAG.py 
done
```

**Qwen2通用聊天API服务** (本地CPU，API代理)
```bash
cd /data/Web-FabGPT/LLM/FabGPT/code/
# 8个Chatbot实例，端口5003,5013,5023,5033,5043,5053,5063,5073
for i in {0..7}; do
  port=$((5003 + i * 10))
  INSTANCE_ID=$i SERVICE_PORT=$port python qwen2_api.py 
done
```

#### 3. 智能负载均衡器集群

**RAG ColPali Manager负载均衡器** (管理远程8个RAG实例)
```bash
cd /data/Web-FabGPT/
python rag_load_balancer.py   # 端口: 5100
```

**Defect缺陷检测负载均衡器** (管理远程8个Defect实例)
```bash
cd /data/Web-FabGPT/
python defect_load_balancer.py   # 端口: 5101
```

**TCAD负载均衡器** (管理本地8个TCAD实例)
```bash
cd /data/Web-FabGPT/
python tcad_load_balancer.py   # 端口: 5102
```

**Circuit电路分析负载均衡器** (管理远程8个Circuit实例)
```bash
cd /data/Web-FabGPT/
python circuit_load_balancer.py   # 端口: 5103
```

**Chatbot聊天负载均衡器** (管理本地8个Chatbot实例)
```bash
cd /data/Web-FabGPT/
python chatbot_load_balancer.py   # 端口: 5104
```

---

### 🔹 远程GPU服务器 (10.98.193.46) 部署

#### 1. RAG ColPali Manager服务集群 (8个GPU实例)

**批量启动脚本**
```bash
#!/bin/bash
cd /data/Web-FabGPT/LLM/RagManager/

echo "🚀 启动8个RAG ColPali Manager实例..."
# 启动8个RAG实例，使用全部8张GPU卡
GPU_ID=0 RAG_MANAGER_PORT=5006 python colpali_rag_manager.py 
GPU_ID=1 RAG_MANAGER_PORT=5016 python colpali_rag_manager.py 
GPU_ID=2 RAG_MANAGER_PORT=5026 python colpali_rag_manager.py 
GPU_ID=3 RAG_MANAGER_PORT=5036 python colpali_rag_manager.py 
GPU_ID=4 RAG_MANAGER_PORT=5046 python colpali_rag_manager.py 
GPU_ID=5 RAG_MANAGER_PORT=5056 python colpali_rag_manager.py 
GPU_ID=6 RAG_MANAGER_PORT=5066 python colpali_rag_manager.py 
GPU_ID=7 RAG_MANAGER_PORT=5076 python colpali_rag_manager.py 

echo "✅ 启动完成 - 8个RAG实例"
echo "📍 端口范围: 5006-5076"
```

#### 2. Circuit电路分析服务集群 (8个GPU实例)

**批量启动脚本**
```bash
#!/bin/bash
cd /data/Web-FabGPT/LLM/Circuit_Think/

echo "🚀 启动8个Circuit分析实例..."
# 启动8个Circuit实例，每个使用一张GPU卡
GPU_ID=0 SERVICE_PORT=5007 python circuit.py 
GPU_ID=1 SERVICE_PORT=5017 python circuit.py 
GPU_ID=2 SERVICE_PORT=5027 python circuit.py 
GPU_ID=3 SERVICE_PORT=5037 python circuit.py 
GPU_ID=4 SERVICE_PORT=5047 python circuit.py 
GPU_ID=5 SERVICE_PORT=5057 python circuit.py 
GPU_ID=6 SERVICE_PORT=5067 python circuit.py 
GPU_ID=7 SERVICE_PORT=5077 python circuit.py 

echo "✅ 启动完成 - 8个Circuit实例"
echo "📍 端口范围: 5007-5077"
```

#### 3. Defect缺陷检测服务集群 (8个GPU实例)

**批量启动脚本**
```bash
#!/bin/bash
cd /data/Web-FabGPT/LLM/FabGPT/code/

echo "🚀 启动8个Defect检测实例..."
# 启动8个Defect实例，每个使用一张GPU卡
GPU_ID=0 SERVICE_PORT=5008 python web_demo_datacenter.py 
GPU_ID=1 SERVICE_PORT=5018 python web_demo_datacenter.py 
GPU_ID=2 SERVICE_PORT=5028 python web_demo_datacenter.py 
GPU_ID=3 SERVICE_PORT=5038 python web_demo_datacenter.py 
GPU_ID=4 SERVICE_PORT=5048 python web_demo_datacenter.py 
GPU_ID=5 SERVICE_PORT=5058 python web_demo_datacenter.py 
GPU_ID=6 SERVICE_PORT=5068 python web_demo_datacenter.py 
GPU_ID=7 SERVICE_PORT=5078 python web_demo_datacenter.py 

echo "✅ 启动完成 - 8个Defect实例"
echo "📍 端口范围: 5008-5078"
```

---

## 📊 完整端口分配表

### 本地服务器 (10.98.64.22) - 统一8路并行
| 服务类型 | 实例 | 端口 | 文件名 | 用途 |
|---------|------|------|-------|------|
| **前端Web** | - | 3000 | npm start | React应用 |
| **用户管理** | - | 8081 | Java Spring Boot | 认证授权 |
| **MySQL** | - | 4306 | ./start.sh | 数据库 |
| **TCAD RAG** | 1-8 | 5002,5012,5022,5032,5042,5052,5062,5072 | TCAD_web_MilvusRAG.py | 技术文档检索 |
| **Qwen2 Chat** | 1-8 | 5003,5013,5023,5033,5043,5053,5063,5073 | qwen2_api.py | 通用聊天代理 |

### 智能负载均衡器 (本地服务器: 10.98.64.22) - 新端口配置
| 负载均衡器 | 端口 | 文件名 | 管理实例 | 用途 |
|-----------|------|-------|---------|------|
| **RAG负载均衡器** | 5100 | rag_load_balancer.py | 远程8个RAG实例 | ColPali文档管理 |
| **Defect负载均衡器** | 5101 | defect_load_balancer.py | 远程8个Defect实例 | 缺陷检测分发 |
| **TCAD负载均衡器** | 5102 | tcad_load_balancer.py | 本地8个TCAD实例 | TCAD服务分发 |
| **Circuit负载均衡器** | 5103 | circuit_load_balancer.py | 远程8个Circuit实例 | 电路分析分发 |
| **Chatbot负载均衡器** | 5104 | chatbot_load_balancer.py | 本地8个Chatbot实例 | 聊天服务分发 |

### 远程GPU服务器 (10.98.193.46) - 8张GPU卡
| 服务类型 | GPU卡 | 端口 | 文件名 | 用途 | 负载均衡器 |
|---------|------|------|-------|------|-----------|
| **RAG Manager** | GPU-0 | 5006 | colpali_rag_manager.py | 知识库管理 | 5100 |
| **RAG Manager** | GPU-1 | 5016 | colpali_rag_manager.py | 知识库管理 | 5100 |
| **RAG Manager** | GPU-2 | 5026 | colpali_rag_manager.py | 知识库管理 | 5100 |
| **RAG Manager** | GPU-3 | 5036 | colpali_rag_manager.py | 知识库管理 | 5100 |
| **RAG Manager** | GPU-4 | 5046 | colpali_rag_manager.py | 知识库管理 | 5100 |
| **RAG Manager** | GPU-5 | 5056 | colpali_rag_manager.py | 知识库管理 | 5100 |
| **RAG Manager** | GPU-6 | 5066 | colpali_rag_manager.py | 知识库管理 | 5100 |
| **RAG Manager** | GPU-7 | 5076 | colpali_rag_manager.py | 知识库管理 | 5100 |
| **Circuit Analysis** | GPU-0 | 5007 | circuit.py | 电路分析 | 5103 |
| **Circuit Analysis** | GPU-1 | 5017 | circuit.py | 电路分析 | 5103 |
| **Circuit Analysis** | GPU-2 | 5027 | circuit.py | 电路分析 | 5103 |
| **Circuit Analysis** | GPU-3 | 5037 | circuit.py | 电路分析 | 5103 |
| **Circuit Analysis** | GPU-4 | 5047 | circuit.py | 电路分析 | 5103 |
| **Circuit Analysis** | GPU-5 | 5057 | circuit.py | 电路分析 | 5103 |
| **Circuit Analysis** | GPU-6 | 5067 | circuit.py | 电路分析 | 5103 |
| **Circuit Analysis** | GPU-7 | 5077 | circuit.py | 电路分析 | 5103 |
| **Defect Detection** | GPU-0 | 5008 | web_demo_datacenter.py | 缺陷检测 | 5101 |
| **Defect Detection** | GPU-1 | 5018 | web_demo_datacenter.py | 缺陷检测 | 5101 |
| **Defect Detection** | GPU-2 | 5028 | web_demo_datacenter.py | 缺陷检测 | 5101 |
| **Defect Detection** | GPU-3 | 5038 | web_demo_datacenter.py | 缺陷检测 | 5101 |
| **Defect Detection** | GPU-4 | 5048 | web_demo_datacenter.py | 缺陷检测 | 5101 |
| **Defect Detection** | GPU-5 | 5058 | web_demo_datacenter.py | 缺陷检测 | 5101 |
| **Defect Detection** | GPU-6 | 5068 | web_demo_datacenter.py | 缺陷检测 | 5101 |
| **Defect Detection** | GPU-7 | 5078 | web_demo_datacenter.py | 缺陷检测 | 5101 |

---

## 🚀 推荐启动顺序

### 第1步: 启动本地基础服务 (10.98.64.22)
```bash
# 1. 启动数据库和用户管理
cd /data/Web-FabGPT/Web/back-end/mysql/  ./start.sh
cd /data/Web-FabGPT/Web/back-end/user-management/  ./start.sh

# 2. 启动本地AI服务集群 (8实例并行)
# TCAD RAG服务
cd /data/Web-FabGPT/LLM/TCAD_code/
for i in {0..7}; do
  port=$((5002 + i * 10))
  INSTANCE_ID=$i SERVICE_PORT=$port python TCAD_web_MilvusRAG.py 
done

# Qwen2聊天服务
cd /data/Web-FabGPT/LLM/FabGPT/code/
for i in {0..7}; do
  port=$((5003 + i * 10))
  INSTANCE_ID=$i SERVICE_PORT=$port python qwen2_api.py 
done

# 3. 启动前端Web服务
cd /data/Web-FabGPT/Web/front-end/Code/  npm start
```

### 第2步: 启动远程GPU AI服务 (10.98.193.46) 
```bash
# 1. 启动RAG集群 (8实例)
cd /data/Web-FabGPT/LLM/RagManager/
for gpu in {0..7}; do
  port=$((5006 + gpu * 10))
  GPU_ID=$gpu RAG_MANAGER_PORT=$port python colpali_rag_manager.py 
done

# 2. 启动Circuit集群 (8实例)
cd /data/Web-FabGPT/LLM/Circuit_Think/
for gpu in {0..7}; do
  port=$((5007 + gpu * 10))
  GPU_ID=$gpu SERVICE_PORT=$port python circuit.py 
done

# 3. 启动Defect集群 (8实例)  
cd /data/Web-FabGPT/LLM/FabGPT/code/
for gpu in {0..7}; do
  port=$((5008 + gpu * 10))
  GPU_ID=$gpu SERVICE_PORT=$port python web_demo_datacenter.py 
done
```

### 第3步: 启动智能负载均衡器 (10.98.64.22)
```bash
cd /data/Web-FabGPT/

# 1. RAG负载均衡器 (管理远程8个RAG实例)
python rag_load_balancer.py 

# 2. Defect负载均衡器 (管理远程8个Defect实例)
python defect_load_balancer.py 

# 3. Circuit负载均衡器 (管理远程8个Circuit实例)
python circuit_load_balancer.py 

# 4. TCAD负载均衡器 (管理本地8个TCAD实例)
python tcad_load_balancer.py 

# 5. Chatbot负载均衡器 (管理本地8个Chatbot实例)
python chatbot_load_balancer.py 
```

---

## 🔧 完成的工作成果

### ✅ 智能负载均衡器集群 (5个) - 新端口配置

1. **`/data/Web-FabGPT/rag_load_balancer.py`**
   - RAG ColPali Manager智能负载均衡器
   - 管理8个远程GPU RAG实例 (5006-5076)
   - 端口: 5100 ✨ **更新后**

2. **`/data/Web-FabGPT/defect_load_balancer.py`** 
   - Defect服务智能负载均衡器
   - 管理8个远程GPU Defect实例 (5008-5078)
   - 端口: 5101 ✨ **更新后**

3. **`/data/Web-FabGPT/circuit_load_balancer.py`**
   - Circuit分析智能负载均衡器
   - 管理8个远程GPU Circuit实例 (5007-5077)
   - 端口: 5103 ✨ **更新后**

4. **`/data/Web-FabGPT/tcad_load_balancer.py`**
   - TCAD负载均衡器
   - 管理8个本地TCAD实例 (5002-5072)
   - 端口: 5102 ✨ **更新后**

5. **`/data/Web-FabGPT/chatbot_load_balancer.py`**
   - Chatbot聊天负载均衡器
   - 管理8个本地Qwen2实例 (5003-5073)
   - 端口: 5104 ✨ **更新后**

### ✅ 数据中心版本AI服务

6. **`/data/Web-FabGPT/LLM/FabGPT/code/web_demo_datacenter.py`**
   - Defect检测服务的数据中心版本
   - 支持环境变量配置 (GPU_ID, SERVICE_PORT)
   - 增强的健康检查和状态管理

### ✅ 前端完整适配

7. **API层完整更新**:
   - `src/api/tcadApi.js` - 新增TCAD负载均衡器API
   - `src/api/circuitApi.js` - 更新Circuit负载均衡器API
   - `src/api/chatBot.js` - 更新Chatbot负载均衡器API
   - `src/api/fabGpt.js` - 更新Defect负载均衡器API
   - `src/api/ragApi.js` - 更新RAG负载均衡器API

8. **页面组件适配**:
   - `src/pages/tcad/index.jsx` - 完整适配TCAD负载均衡器
   - `src/pages/CircuitThink/index.jsx` - 适配Circuit负载均衡器
   - `src/pages/chatBot/index.jsx` - 适配Chatbot负载均衡器
   - `src/pages/fab/fabGPT/index.jsx` - 适配Defect负载均衡器

9. **前端代码清理**:
   - 清除所有备份文件 (.bak, guangke_bak等)
   - 移除过期的负载均衡器工具文件
   - 删除不需要的组件目录

---

## 🔍 系统监控和验证

### 健康检查命令
```bash
# 检查本地8路并行服务
echo "=== 本地服务状态检查 ==="
for service in "TCAD" "Chat"; do
  echo "--- $service 服务 (8实例) ---"
  if [ "$service" = "TCAD" ]; then
    ports=(5002 5012 5022 5032 5042 5052 5062 5072)
  else
    ports=(5003 5013 5023 5033 5043 5053 5063 5073)
  fi
  
  for port in "${ports[@]}"; do
    status=$(curl -s http://10.98.64.22:$port/health 2>/dev/null | jq -r '.status' 2>/dev/null || echo "unreachable")
    echo "  实例 端口$port: $status"
  done
done

# 检查远程GPU服务器所有实例
echo "=== 远程GPU服务状态检查 ==="
for service in "RAG" "Circuit" "Defect"; do
  echo "--- $service 服务 (8实例) ---"
  if [ "$service" = "RAG" ]; then
    ports=(5006 5016 5026 5036 5046 5056 5066 5076)
  elif [ "$service" = "Circuit" ]; then
    ports=(5007 5017 5027 5037 5047 5057 5067 5077)
  else
    ports=(5008 5018 5028 5038 5048 5058 5068 5078)
  fi
  
  for port in "${ports[@]}"; do
    status=$(curl -s http://10.98.193.46:$port/health | jq -r '.status' 2>/dev/null || echo "unreachable")
    echo "  GPU实例 端口$port: $status"
  done
done

# 检查负载均衡器状态
echo "=== 负载均衡器状态 ==="
curl -s http://10.98.64.22:5100/status | jq .  # RAG负载均衡器
curl -s http://10.98.64.22:5101/status | jq .  # Defect负载均衡器
curl -s http://10.98.64.22:5102/status | jq .  # TCAD负载均衡器
curl -s http://10.98.64.22:5103/status | jq .  # Circuit负载均衡器
curl -s http://10.98.64.22:5104/status | jq .  # Chatbot负载均衡器
```

### 批量服务管理脚本
```bash
# 创建服务管理脚本目录
mkdir -p /data/Web-FabGPT/scripts

# 本地服务启动脚本
cat > /data/Web-FabGPT/scripts/start_local_services.sh << 'EOF'
#!/bin/bash
cd /data/Web-FabGPT

echo "🚀 启动本地AI服务集群..."

# 启动TCAD服务 (8实例)
cd /data/Web-FabGPT/LLM/TCAD_code/
for i in {0..7}; do
  port=$((5002 + i * 10))
  INSTANCE_ID=$i SERVICE_PORT=$port python TCAD_web_MilvusRAG.py 
  echo "  TCAD实例-$i 启动 (端口:$port)"
done

# 启动Chatbot服务 (8实例)
cd /data/Web-FabGPT/LLM/FabGPT/code/
for i in {0..7}; do
  port=$((5003 + i * 10))
  INSTANCE_ID=$i SERVICE_PORT=$port python qwen2_api.py 
  echo "  Chatbot实例-$i 启动 (端口:$port)"
done

echo "✅ 本地AI服务集群启动完成"
EOF

# 负载均衡器启动脚本
cat > /data/Web-FabGPT/scripts/start_load_balancers.sh << 'EOF'
#!/bin/bash
cd /data/Web-FabGPT

echo "🚀 启动智能负载均衡器集群..."

python rag_load_balancer.py 
echo "  RAG负载均衡器启动 (端口:5100)"

python defect_load_balancer.py 
echo "  Defect负载均衡器启动 (端口:5101)"

python tcad_load_balancer.py 
echo "  TCAD负载均衡器启动 (端口:5102)"

python circuit_load_balancer.py 
echo "  Circuit负载均衡器启动 (端口:5103)"

python chatbot_load_balancer.py 
echo "  Chatbot负载均衡器启动 (端口:5104)"

echo "✅ 智能负载均衡器集群启动完成"
EOF

chmod +x /data/Web-FabGPT/scripts/*.sh
```

---

## ⚠️ 部署注意事项

### 1. 资源管理
- **GPU内存**: 远程服务器每张GPU卡运行3个服务实例 (RAG+Circuit+Defect)
- **CPU资源**: 本地服务器运行16个AI实例 (8×TCAD + 8×Chatbot) + 5个负载均衡器
- **网络带宽**: 本地↔远程服务器间大量数据传输，建议千兆网络

### 2. 端口规划
- **本地服务器**: 3000, 4306, 5000-5073, 8080-8081 (约79个端口)
- **远程GPU服务器**: 5006-5078 (24个端口)
- **端口冲突检查**: 使用`netstat -tuln`检查端口占用

### 3. 故障处理
- **负载均衡器监控**: 每个负载均衡器都有`/health`和`/status`端点
- **实例自动恢复**: 健康检查失败时自动切换到可用实例
- **日志监控**: 所有服务都有详细的日志输出

---

## 🎉 最终架构优势

### 🚀 性能优势
- **40个AI实例**: 8×RAG + 8×Circuit + 8×Defect + 8×TCAD + 8×Chatbot
- **5个智能负载均衡器**: 自动选择最优实例，响应时间最短
- **8张GPU卡全利用**: 远程服务器GPU资源充分利用

### 🛡️ 稳定性优势
- **故障自动恢复**: 实例健康检查，自动切换故障实例
- **服务隔离**: 本地vs远程，用户会话独立
- **负载分散**: 避免单点瓶颈，提高系统可用性

### 📈 扩展性优势
- **水平扩展**: 可轻松添加更多GPU实例或本地实例
- **模块化设计**: 各AI服务独立部署和扩展
- **智能路由**: 负载均衡器自动适配新增实例

### 💻 前端用户体验
- **统一API接口**: 前端通过负载均衡器透明访问所有AI服务
- **智能分发**: 用户请求自动路由到最优实例
- **故障无感知**: 实例故障时前端无需感知，自动切换

这个架构实现了Web-FabGPT系统的完整智能化部署，提供了最佳的性能、可靠性和扩展性。

---

## 🔄 **重要更新说明**

### **端口配置优化 (2025-08-26)**
为了解决端口冲突问题，智能负载均衡器端口已重新分配到5100-5199范围：

#### **更新前 → 更新后**
- RAG负载均衡器: `5000` → `5100` 
- Defect负载均衡器: `5001` → `5101`
- TCAD负载均衡器: `5004` → `5102`
- Circuit负载均衡器: `5005` → `5103`
- Chatbot负载均衡器: `5008` → `5104`

#### **同步更新的组件**
- ✅ 前端API配置 (`src/utils/apiUtils.js`)
- ✅ 前端组件引用 (`src/components/container/container.jsx`)
- ✅ 后端服务RAG连接 (TCAD服务、Chatbot服务)
- ✅ RAG负载均衡器完整端点支持 (新增文档上传、管理等端点)
- ✅ GPU设备分配修复 (RAG Manager正确使用指定GPU)

### **新功能亮点**
1. **RAG完整端点代理**: RAG负载均衡器现支持所有RAG操作的透明代理
2. **智能GPU分配**: RAG实例现在正确使用CUDA_VISIBLE_DEVICES进行GPU隔离
3. **统一前端接口**: 所有AI服务通过统一的负载均衡器入口访问
4. **增强错误处理**: 改进了各服务间的连接和错误恢复机制