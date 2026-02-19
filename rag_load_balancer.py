#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RAG ColPali Manager 服务智能负载均衡器
管理8个远程GPU实例的RAG知识库服务负载分发
"""

import asyncio
import aiohttp
import time
import json
import logging
from flask import Flask, request, Response, jsonify
from flask_cors import CORS
import requests
from concurrent.futures import ThreadPoolExecutor
import threading

# 配置日志
logging.basicConfig(level=logging.INFO, 
                   format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("RAG-LoadBalancer")
logging.getLogger('werkzeug').setLevel(logging.WARNING)

app = Flask(__name__)
CORS(app)

class RAGLoadBalancer:
    def __init__(self):
        # 远程A100服务器配置
        self.remote_server = "10.98.193.46"  # 恢复原始内网IP
        # 8个RAG实例端口
        # self.rag_ports = [5006, 5016, 5026, 5036, 5046, 5056, 5066, 5076]
        self.rag_ports = [5006]
        
        # 实例状态管理
        self.instance_status = {}
        self.last_health_check = {}
        self.health_check_interval = 5  # 5秒检查间隔
        self.request_timeout = 30  # 30秒请求超时 (RAG处理可能较慢)
        
        # 负载均衡策略
        self.current_instance = 0
        self.request_counts = {port: 0 for port in self.rag_ports}
        
        # 用户会话亲和性 - 确保同一用户的请求路由到同一实例
        self.user_instance_map = {}  # {user_id: port}
        self.instance_user_count = {port: 0 for port in self.rag_ports}  # 每个实例的用户数
        
        # 线程池
        self.executor = ThreadPoolExecutor(max_workers=8)
        
        # 启动健康检查
        self.start_health_check()
        
        logger.info(f"✅ RAG负载均衡器初始化完成")
        logger.info(f"📍 管理实例: {len(self.rag_ports)}个")
        logger.info(f"🔗 端口范围: {min(self.rag_ports)}-{max(self.rag_ports)}")
        
    def start_health_check(self):
        """启动异步健康检查线程"""
        def health_check_worker():
            while True:
                self.check_all_instances_health()
                time.sleep(self.health_check_interval)
        
        health_thread = threading.Thread(target=health_check_worker, daemon=True)
        health_thread.start()
        logger.info("🏥 健康检查线程启动")
    
    def check_all_instances_health(self):
        """检查所有实例健康状态"""
        current_time = time.time()
        
        def check_single_instance(port):
            try:
                url = f"http://{self.remote_server}:{port}/health"
                response = requests.get(url, timeout=3)
                
                if response.status_code == 200:
                    status_data = response.json()
                    self.instance_status[port] = {
                        'healthy': True,
                        'busy': status_data.get('busy', False),
                        'gpu_id': status_data.get('gpu_id', -1),
                        'last_request_time': status_data.get('last_request_time', 0),
                        'total_requests': status_data.get('total_requests', 0),
                        'response_time': response.elapsed.total_seconds(),
                        'vector_db_status': status_data.get('vector_db_status', 'unknown')
                    }
                else:
                    self.instance_status[port] = {
                        'healthy': False,
                        'busy': True,
                        'error': f'HTTP {response.status_code}'
                    }
                    
            except Exception as e:
                self.instance_status[port] = {
                    'healthy': False,
                    'busy': True,
                    'error': str(e)
                }
                
            self.last_health_check[port] = current_time
        
        # 并行检查所有实例
        futures = [self.executor.submit(check_single_instance, port) 
                  for port in self.rag_ports]
        
        # 等待所有检查完成
        for future in futures:
            try:
                future.result(timeout=5)
            except Exception as e:
                logger.error(f"RAG健康检查异常: {e}")
    
    def get_instance_for_user(self, user_id):
        """获取用户绑定的实例，如果不存在则分配新实例"""
        if not user_id:
            user_id = 'anonymous'
            
        # 如果用户已有绑定实例，检查该实例是否健康
        if user_id in self.user_instance_map:
            bound_port = self.user_instance_map[user_id]
            status = self.instance_status.get(bound_port, {})
            if status.get('healthy', False):
                logger.debug(f"🔗 用户 {user_id} 使用已绑定实例: GPU-{status.get('gpu_id', '?')}")
                return bound_port
            else:
                # 绑定实例不健康，需要重新分配
                logger.warning(f"⚠️ 用户 {user_id} 绑定实例不健康，重新分配")
                self.instance_user_count[bound_port] -= 1
                del self.user_instance_map[user_id]
        
        # 为用户分配新实例
        best_port = self.get_best_instance_for_new_user()
        self.user_instance_map[user_id] = best_port
        self.instance_user_count[best_port] += 1
        
        status = self.instance_status.get(best_port, {})
        logger.info(f"🎯 用户 {user_id} 分配到新实例: GPU-{status.get('gpu_id', '?')} (端口{best_port})")
        return best_port
    
    def get_best_instance_for_new_user(self):
        """为新用户选择最佳实例 - 优先考虑用户负载均衡"""
        available_instances = []
        
        # 筛选健康实例
        for port in self.rag_ports:
            status = self.instance_status.get(port, {})
            if status.get('healthy', False):
                available_instances.append({
                    'port': port,
                    'user_count': self.instance_user_count[port],
                    'response_time': status.get('response_time', 1.0),
                    'busy': status.get('busy', True)
                })
        
        if not available_instances:
            # 如果没有健康实例，使用轮询
            self.current_instance = (self.current_instance + 1) % len(self.rag_ports)
            return self.rag_ports[self.current_instance]
        
        # 选择用户数最少的健康实例，如果用户数相同则选择响应时间最短的
        best_instance = min(available_instances, 
                          key=lambda x: (x['user_count'], x['response_time'], x['busy']))
        return best_instance['port']
    
    def get_best_instance(self):
        """智能选择最佳RAG实例"""
        available_instances = []
        
        # 筛选健康且不忙碌的实例
        for port in self.rag_ports:
            status = self.instance_status.get(port, {})
            if status.get('healthy', False) and not status.get('busy', True):
                available_instances.append({
                    'port': port,
                    'response_time': status.get('response_time', 1.0),
                    'request_count': self.request_counts[port],
                    'vector_db_status': status.get('vector_db_status', 'unknown')
                })
        
        if not available_instances:
            # 如果没有可用实例，选择最少请求的健康实例
            healthy_instances = [port for port in self.rag_ports 
                               if self.instance_status.get(port, {}).get('healthy', False)]
            
            if healthy_instances:
                # 选择请求数最少的实例
                best_port = min(healthy_instances, key=lambda p: self.request_counts[p])
                logger.warning(f"⚠️ 所有RAG实例繁忙，选择最少负载实例: {best_port}")
                return best_port
            else:
                # 所有实例都不健康，使用轮询策略
                self.current_instance = (self.current_instance + 1) % len(self.rag_ports)
                selected_port = self.rag_ports[self.current_instance]
                logger.warning(f"⚠️ 所有RAG实例不健康，使用轮询策略: {selected_port}")
                return selected_port
        
        # 选择响应时间最短且请求数较少的实例
        best_instance = min(available_instances, 
                          key=lambda x: (x['response_time'] * 0.6 + x['request_count'] * 0.4))
        
        selected_port = best_instance['port']
        logger.info(f"🎯 选择最佳RAG实例: GPU-{self.instance_status[selected_port].get('gpu_id', '?')} (端口{selected_port})")
        return selected_port
    
    def proxy_request(self, target_port, endpoint, method='POST', **kwargs):
        """代理请求到目标实例"""
        return self.proxy_request_with_timeout(target_port, endpoint, method, self.request_timeout, **kwargs)
    
    def proxy_request_with_timeout(self, target_port, endpoint, method='POST', timeout=30, **kwargs):
        """代理请求到目标实例 (自定义超时时间)"""
        target_url = f"http://{self.remote_server}:{target_port}{endpoint}"
        
        try:
            # 记录请求
            self.request_counts[target_port] += 1
            
            # 转发请求
            if method == 'POST':
                response = requests.post(target_url, timeout=timeout, **kwargs)
            elif method == 'GET':
                response = requests.get(target_url, timeout=timeout, **kwargs)
            elif method == 'DELETE':
                response = requests.delete(target_url, timeout=timeout, **kwargs)
            else:
                response = requests.request(method, target_url, timeout=timeout, **kwargs)
            
            logger.info(f"✅ RAG请求成功代理: {endpoint} -> GPU-{self.instance_status.get(target_port, {}).get('gpu_id', '?')}")
            return response
            
        except requests.exceptions.Timeout:
            logger.error(f"⏰ RAG请求超时: {target_url}")
            raise
        except requests.exceptions.RequestException as e:
            logger.error(f"❌ RAG请求失败: {target_url}, 错误: {e}")
            raise
    
    def get_cluster_stats(self):
        """获取RAG集群统计信息"""
        stats = {
            'total_instances': len(self.rag_ports),
            'healthy_instances': 0,
            'busy_instances': 0,
            'total_requests': sum(self.request_counts.values()),
            'instances': []
        }
        
        for port in self.rag_ports:
            status = self.instance_status.get(port, {})
            instance_info = {
                'port': port,
                'gpu_id': status.get('gpu_id', -1),
                'healthy': status.get('healthy', False),
                'busy': status.get('busy', True),
                'request_count': self.request_counts[port],
                'response_time': status.get('response_time', 0),
                'vector_db_status': status.get('vector_db_status', 'unknown'),
                'last_check': self.last_health_check.get(port, 0)
            }
            
            if status.get('error'):
                instance_info['error'] = status['error']
            
            stats['instances'].append(instance_info)
            
            if status.get('healthy', False):
                stats['healthy_instances'] += 1
            if status.get('busy', True):
                stats['busy_instances'] += 1
        
        return stats

# 创建全局负载均衡器实例
load_balancer = RAGLoadBalancer()

@app.route('/health', methods=['GET'])
def health_check():
    """负载均衡器健康检查"""
    return jsonify({
        'status': 'healthy',
        'service': 'rag-load-balancer',
        'timestamp': time.time(),
        'managed_instances': len(load_balancer.rag_ports)
    })

@app.route('/status', methods=['GET'])  
def get_status():
    """获取RAG集群状态"""
    return jsonify(load_balancer.get_cluster_stats())

@app.route('/generate', methods=['POST'])
def proxy_generate():
    """代理RAG生成请求到最佳实例"""
    try:
        # 选择最佳实例
        target_port = load_balancer.get_best_instance()
        
        # 准备代理参数
        proxy_kwargs = {}
        
        # 处理JSON数据
        if request.is_json:
            proxy_kwargs['json'] = request.get_json()
        else:
            proxy_kwargs['data'] = request.data
        
        # 转发请求头
        headers = {}
        for key, value in request.headers:
            if key.lower() not in ['host', 'content-length', 'connection']:
                headers[key] = value
        
        proxy_kwargs['headers'] = headers
        
        # 代理请求
        response = load_balancer.proxy_request(target_port, '/generate', 'POST', **proxy_kwargs)
        
        # 返回响应
        return Response(
            response.content,
            status=response.status_code,
            headers=dict(response.headers)
        )
        
    except Exception as e:
        logger.error(f"❌ RAG代理请求失败: {e}")
        return jsonify({'error': 'RAG service temporarily unavailable'}), 503

@app.route('/stream_generate', methods=['POST'])  
def proxy_stream_generate():
    """代理流式RAG生成请求"""
    try:
        target_port = load_balancer.get_best_instance()
        
        # 转发原始请求体和头
        proxy_kwargs = {
            'json' if request.is_json else 'data': request.get_json() if request.is_json else request.data,
            'headers': {key: value for key, value in request.headers 
                       if key.lower() not in ['host', 'content-length', 'connection']},
            'stream': True  # 重要：启用流式响应
        }
        
        response = load_balancer.proxy_request(target_port, '/stream_generate', 'POST', **proxy_kwargs)
        
        # 流式转发响应
        def generate():
            for chunk in response.iter_content(chunk_size=1024):
                if chunk:
                    yield chunk
        
        return Response(generate(), 
                       status=response.status_code,
                       headers=dict(response.headers))
        
    except Exception as e:
        logger.error(f"❌ RAG流式代理失败: {e}")
        return jsonify({'error': 'RAG streaming service temporarily unavailable'}), 503

@app.route('/uploadFile', methods=['POST'])
def proxy_upload_file():
    """代理文件上传到最佳RAG实例"""
    try:
        target_port = load_balancer.get_best_instance()
        
        # 处理文件上传
        files = {}
        data = {}
        
        for key, file in request.files.items():
            files[key] = (file.filename, file.stream, file.content_type)
        
        for key, value in request.form.items():
            data[key] = value
        
        proxy_kwargs = {
            'files': files,
            'data': data,
            'headers': {key: value for key, value in request.headers 
                       if key.lower() not in ['host', 'content-length', 'connection', 'content-type']}
        }
        
        response = load_balancer.proxy_request(target_port, '/uploadFile', 'POST', **proxy_kwargs)
        
        return Response(
            response.content,
            status=response.status_code,
            headers=dict(response.headers)
        )
        
    except Exception as e:
        logger.error(f"❌ RAG文件上传代理失败: {e}")
        return jsonify({'error': 'RAG file upload service temporarily unavailable'}), 503

@app.route('/deleteFile', methods=['POST'])
def proxy_delete_file():
    """代理文件删除请求"""
    try:
        target_port = load_balancer.get_best_instance()
        
        proxy_kwargs = {
            'json' if request.is_json else 'data': request.get_json() if request.is_json else request.data,
            'headers': {key: value for key, value in request.headers 
                       if key.lower() not in ['host', 'content-length', 'connection']}
        }
        
        response = load_balancer.proxy_request(target_port, '/deleteFile', 'POST', **proxy_kwargs)
        
        return Response(
            response.content,
            status=response.status_code,
            headers=dict(response.headers)
        )
        
    except Exception as e:
        logger.error(f"❌ RAG文件删除代理失败: {e}")
        return jsonify({'error': 'RAG file deletion service temporarily unavailable'}), 503

@app.route('/upload_rag_document', methods=['POST'])
def proxy_upload_rag_document():
    """代理RAG文档上传到用户绑定实例"""
    try:
        # 从请求中提取用户ID
        user_id = request.form.get('user_id', 'anonymous')
        target_port = load_balancer.get_instance_for_user(user_id)
        
        # 处理文件上传
        files = {}
        data = {}
        
        for key, file in request.files.items():
            files[key] = (file.filename, file.stream, file.content_type)
        
        for key, value in request.form.items():
            data[key] = value
        
        proxy_kwargs = {
            'files': files,
            'data': data,
            'headers': {key: value for key, value in request.headers 
                       if key.lower() not in ['host', 'content-length', 'connection', 'content-type']}
        }
        
        logger.info(f"📤 用户 {user_id} 文档上传路由到: GPU-{load_balancer.instance_status.get(target_port, {}).get('gpu_id', '?')}")
        
        response = load_balancer.proxy_request(target_port, '/upload_rag_document', 'POST', **proxy_kwargs)
        
        return Response(
            response.content,
            status=response.status_code,
            headers=dict(response.headers)
        )
        
    except Exception as e:
        logger.error(f"❌ RAG文档上传代理失败: {e}")
        return jsonify({'error': 'RAG document upload service temporarily unavailable'}), 503

@app.route('/delete_rag_document', methods=['POST'])
def proxy_delete_rag_document():
    """代理RAG文档删除请求"""
    try:
        target_port = load_balancer.get_best_instance()
        
        proxy_kwargs = {
            'json' if request.is_json else 'data': request.get_json() if request.is_json else request.data,
            'headers': {key: value for key, value in request.headers 
                       if key.lower() not in ['host', 'content-length', 'connection']}
        }
        
        response = load_balancer.proxy_request(target_port, '/delete_rag_document', 'POST', **proxy_kwargs)
        
        return Response(
            response.content,
            status=response.status_code,
            headers=dict(response.headers)
        )
        
    except Exception as e:
        logger.error(f"❌ RAG文档删除代理失败: {e}")
        return jsonify({'error': 'RAG document deletion service temporarily unavailable'}), 503

@app.route('/get_rag_configurations', methods=['GET'])
def proxy_get_rag_configurations():
    """代理获取RAG配置列表"""
    try:
        target_port = load_balancer.get_best_instance()
        
        # 转发查询参数
        query_string = request.query_string.decode('utf-8')
        endpoint = '/get_rag_configurations'
        if query_string:
            endpoint += f'?{query_string}'
        
        proxy_kwargs = {
            'headers': {key: value for key, value in request.headers 
                       if key.lower() not in ['host', 'content-length', 'connection']}
        }
        
        response = load_balancer.proxy_request(target_port, endpoint, 'GET', **proxy_kwargs)
        
        return Response(
            response.content,
            status=response.status_code,
            headers=dict(response.headers)
        )
        
    except Exception as e:
        logger.error(f"❌ RAG配置获取代理失败: {e}")
        return jsonify({'error': 'RAG configuration service temporarily unavailable'}), 503

@app.route('/get_rag_documents', methods=['GET'])
def proxy_get_rag_documents():
    """代理获取RAG文档列表"""
    try:
        # 从查询参数中提取用户ID，使用用户绑定实例
        user_id = request.args.get('user_id', 'anonymous')
        target_port = load_balancer.get_instance_for_user(user_id)
        
        # 转发查询参数
        query_string = request.query_string.decode('utf-8')
        endpoint = '/get_rag_documents'
        if query_string:
            endpoint += f'?{query_string}'
        
        proxy_kwargs = {
            'headers': {key: value for key, value in request.headers 
                       if key.lower() not in ['host', 'content-length', 'connection']}
        }
        
        logger.debug(f"📄 用户 {user_id} 文档列表查询路由到: GPU-{load_balancer.instance_status.get(target_port, {}).get('gpu_id', '?')}")
        
        response = load_balancer.proxy_request(target_port, endpoint, 'GET', **proxy_kwargs)
        
        return Response(
            response.content,
            status=response.status_code,
            headers=dict(response.headers)
        )
        
    except Exception as e:
        logger.error(f"❌ RAG文档列表代理失败: {e}")
        return jsonify({'error': 'RAG document list service temporarily unavailable'}), 503

@app.route('/set_active_configuration', methods=['POST'])
def proxy_set_active_configuration():
    """代理设置活跃RAG配置 - 使用用户绑定实例"""
    try:
        # 从请求中提取用户ID，确保路由到用户绑定的实例
        request_data = request.get_json() if request.is_json else {}
        user_id = request_data.get('user_id', 'anonymous')
        config_id = request_data.get('config_id', 'unknown')
        
        # 使用用户绑定实例而不是随机选择
        target_port = load_balancer.get_instance_for_user(user_id)
        
        logger.info(f"⚙️  RAG配置切换路由: 用户 {user_id} -> 实例 {target_port}, 配置: {config_id}")
        
        proxy_kwargs = {
            'json' if request.is_json else 'data': request.get_json() if request.is_json else request.data,
            'headers': {key: value for key, value in request.headers 
                       if key.lower() not in ['host', 'content-length', 'connection']}
        }
        
        response = load_balancer.proxy_request(target_port, '/set_active_configuration', 'POST', **proxy_kwargs)
        
        return Response(
            response.content,
            status=response.status_code,
            headers=dict(response.headers)
        )
        
    except Exception as e:
        logger.error(f"❌ RAG配置设置代理失败: {e}")
        return jsonify({'error': 'RAG configuration setting service temporarily unavailable'}), 503

@app.route('/get_user_active_config', methods=['GET'])
def proxy_get_user_active_config():
    """代理获取用户活跃配置 - 使用用户绑定实例"""
    try:
        # 从查询参数中提取用户ID
        user_id = request.args.get('user_id', 'anonymous')
        
        # 使用用户绑定实例
        target_port = load_balancer.get_instance_for_user(user_id)
        
        logger.info(f"👤 用户活跃配置查询路由: 用户 {user_id} -> 实例 {target_port}")
        
        # 转发查询参数
        query_string = request.query_string.decode('utf-8')
        endpoint = '/get_user_active_config'
        if query_string:
            endpoint += f'?{query_string}'
        
        proxy_kwargs = {
            'headers': {key: value for key, value in request.headers 
                       if key.lower() not in ['host', 'content-length', 'connection']}
        }
        
        response = load_balancer.proxy_request(target_port, endpoint, 'GET', **proxy_kwargs)
        
        return Response(
            response.content,
            status=response.status_code,
            headers=dict(response.headers)
        )
        
    except Exception as e:
        logger.error(f"❌ 用户活跃配置查询代理失败: {e}")
        return jsonify({'error': 'User active config service temporarily unavailable'}), 503

@app.route('/create_rag_configuration', methods=['POST'])
def proxy_create_rag_configuration():
    """代理创建RAG配置"""
    try:
        target_port = load_balancer.get_best_instance()
        
        proxy_kwargs = {
            'json' if request.is_json else 'data': request.get_json() if request.is_json else request.data,
            'headers': {key: value for key, value in request.headers 
                       if key.lower() not in ['host', 'content-length', 'connection']}
        }
        
        response = load_balancer.proxy_request(target_port, '/create_rag_configuration', 'POST', **proxy_kwargs)
        
        return Response(
            response.content,
            status=response.status_code,
            headers=dict(response.headers)
        )
        
    except Exception as e:
        logger.error(f"❌ RAG配置创建代理失败: {e}")
        return jsonify({'error': 'RAG configuration creation service temporarily unavailable'}), 503

@app.route('/delete_rag_configuration', methods=['POST'])
def proxy_delete_rag_configuration():
    """代理删除RAG配置"""
    try:
        target_port = load_balancer.get_best_instance()
        
        proxy_kwargs = {
            'json' if request.is_json else 'data': request.get_json() if request.is_json else request.data,
            'headers': {key: value for key, value in request.headers 
                       if key.lower() not in ['host', 'content-length', 'connection']}
        }
        
        # 删除操作需要更长的超时时间 (需要跨8个实例同步)
        response = load_balancer.proxy_request_with_timeout(target_port, '/delete_rag_configuration', 'POST', 60, **proxy_kwargs)
        
        return Response(
            response.content,
            status=response.status_code,
            headers=dict(response.headers)
        )
        
    except requests.exceptions.Timeout:
        logger.error("⏰ RAG配置删除超时，但操作可能在后台继续进行")
        return jsonify({
            'message': '删除操作正在进行中，请稍后刷新页面查看结果',
            'success': True,  # 标记为成功，让前端显示友好信息
            'timeout': True
        }), 200
    except Exception as e:
        logger.error(f"❌ RAG配置删除代理失败: {e}")
        return jsonify({'error': 'RAG configuration deletion service temporarily unavailable'}), 503

@app.route('/get_relevant_context', methods=['POST'])
def proxy_get_relevant_context():
    """代理获取相关上下文 - 使用用户绑定实例"""
    try:
        # 从请求中提取用户ID，确保路由到用户绑定的实例
        request_data = request.get_json() if request.is_json else {}
        user_id = request_data.get('user_id', 'anonymous')
        
        # 使用用户绑定实例而不是随机选择
        target_port = load_balancer.get_instance_for_user(user_id)
        
        logger.info(f"🔍 RAG上下文查询路由: 用户 {user_id} -> 实例 {target_port}")
        
        proxy_kwargs = {
            'json' if request.is_json else 'data': request.get_json() if request.is_json else request.data,
            'headers': {key: value for key, value in request.headers 
                       if key.lower() not in ['host', 'content-length', 'connection']}
        }
        
        response = load_balancer.proxy_request(target_port, '/get_relevant_context', 'POST', **proxy_kwargs)
        
        return Response(
            response.content,
            status=response.status_code,
            headers=dict(response.headers)
        )
        
    except Exception as e:
        logger.error(f"❌ RAG上下文获取代理失败: {e}")
        return jsonify({'error': 'RAG context service temporarily unavailable'}), 503

@app.route('/listConfigurations', methods=['GET'])
def proxy_list_configurations():
    """代理RAG配置列表请求"""
    try:
        # 对于读取操作，可以使用任意健康实例
        target_port = load_balancer.get_best_instance()
        
        proxy_kwargs = {
            'headers': {key: value for key, value in request.headers 
                       if key.lower() not in ['host', 'content-length', 'connection']}
        }
        
        response = load_balancer.proxy_request(target_port, '/listConfigurations', 'GET', **proxy_kwargs)
        
        return Response(
            response.content,
            status=response.status_code,
            headers=dict(response.headers)
        )
        
    except Exception as e:
        logger.error(f"❌ RAG配置列表代理失败: {e}")
        return jsonify({'error': 'RAG configuration service temporarily unavailable'}), 503

@app.route('/save_user_session_state', methods=['POST'])
def proxy_save_user_session_state():
    """代理保存用户会话状态请求"""
    try:
        # 选择最佳实例处理保存请求
        target_port = load_balancer.get_best_instance()
        
        proxy_kwargs = {
            'headers': {key: value for key, value in request.headers 
                       if key.lower() not in ['host', 'content-length', 'connection']},
            'data': request.get_data(),
        }
        
        response = load_balancer.proxy_request(target_port, '/save_user_session_state', 'POST', **proxy_kwargs)
        
        return Response(
            response.content,
            status=response.status_code,
            headers=dict(response.headers)
        )
        
    except Exception as e:
        logger.error(f"❌ 保存会话状态代理失败: {e}")
        return jsonify({'error': 'Session state service temporarily unavailable'}), 503

@app.route('/get_user_session_state', methods=['GET'])
def proxy_get_user_session_state():
    """代理获取用户会话状态请求"""
    try:
        # 对于读取操作，可以使用任意健康实例
        target_port = load_balancer.get_best_instance()
        
        query_string = request.query_string.decode('utf-8')
        endpoint = f'/get_user_session_state?{query_string}' if query_string else '/get_user_session_state'
        
        proxy_kwargs = {
            'headers': {key: value for key, value in request.headers 
                       if key.lower() not in ['host', 'content-length', 'connection']}
        }
        
        response = load_balancer.proxy_request(target_port, endpoint, 'GET', **proxy_kwargs)
        
        return Response(
            response.content,
            status=response.status_code,
            headers=dict(response.headers)
        )
        
    except Exception as e:
        logger.error(f"❌ 获取会话状态代理失败: {e}")
        return jsonify({'error': 'Session state service temporarily unavailable'}), 503

@app.route('/get_user_tasks', methods=['GET'])
def proxy_get_user_tasks():
    """代理获取用户任务请求 - 优先查询用户绑定实例，失败时查询所有实例"""
    try:
        query_string = request.query_string.decode('utf-8')
        endpoint = f'/get_user_tasks?{query_string}' if query_string else '/get_user_tasks'
        
        # 提取用户ID用于实例绑定
        user_id = request.args.get('user_id', 'anonymous')
        
        proxy_kwargs = {
            'headers': {key: value for key, value in request.headers 
                       if key.lower() not in ['host', 'content-length', 'connection']}
        }
        
        # 优先从用户绑定的实例获取任务
        try:
            user_port = load_balancer.get_instance_for_user(user_id)
            if user_port in load_balancer.instance_status and load_balancer.instance_status[user_port].get('healthy', False):
                response = load_balancer.proxy_request(user_port, endpoint, 'GET', **proxy_kwargs)
                if response.status_code == 200:
                    data = response.json()
                    if 'tasks' in data and data['tasks']:
                        # 从用户实例成功获取到任务
                        logger.info(f"✅ 从用户绑定实例获取任务: {user_id} -> GPU-{load_balancer.instance_status[user_port].get('gpu_id', '?')}")
                        return jsonify(data)
        except Exception as e:
            logger.debug(f"从用户绑定实例获取任务失败: {e}")
        
        # 如果用户绑定实例没有找到任务，查询所有健康实例
        logger.debug(f"查询所有实例获取用户 {user_id} 的任务")
        all_tasks = []
        successful_responses = 0
        
        # 查询所有健康实例
        for port in load_balancer.rag_ports:
            if port in load_balancer.instance_status and load_balancer.instance_status[port].get('healthy', False):
                try:
                    response = load_balancer.proxy_request(port, endpoint, 'GET', **proxy_kwargs)
                    if response.status_code == 200:
                        data = response.json()
                        if 'tasks' in data and isinstance(data['tasks'], list):
                            all_tasks.extend(data['tasks'])
                        successful_responses += 1
                except Exception as e:
                    logger.debug(f"查询实例{port}失败: {e}")
                    continue
        
        # 去重任务（基于task_id和doc_id）
        unique_tasks = {}
        for task in all_tasks:
            # 使用task_id或doc_id作为唯一标识
            task_key = task.get('task_id') or task.get('doc_id')
            if task_key:
                # 保留最新的任务状态（如果有多个重复）
                if task_key not in unique_tasks or task.get('progress', 0) > unique_tasks[task_key].get('progress', 0):
                    unique_tasks[task_key] = task
        
        final_tasks = list(unique_tasks.values())
        
        logger.info(f"🔍 查询所有实例获取用户 {user_id} 任务: {len(final_tasks)}个任务，{successful_responses}个实例响应")
        
        return jsonify({
            'tasks': final_tasks,
            'count': len(final_tasks),
            'queried_instances': successful_responses
        })
        
    except Exception as e:
        logger.error(f"❌ 获取用户任务代理失败: {e}")
        return jsonify({'error': 'User tasks service temporarily unavailable'}), 503

@app.route('/check_processing_progress', methods=['GET'])
def proxy_check_processing_progress():
    """代理检查处理进度请求 - 查询所有实例找到相关任务"""
    try:
        query_string = request.query_string.decode('utf-8')
        endpoint = f'/check_processing_progress?{query_string}' if query_string else '/check_processing_progress'
        
        proxy_kwargs = {
            'headers': {key: value for key, value in request.headers 
                       if key.lower() not in ['host', 'content-length', 'connection']}
        }
        
        # 查询所有健康实例，找到有相关任务的实例
        for port in load_balancer.rag_ports:
            if port in load_balancer.instance_status and load_balancer.instance_status[port].get('healthy', False):
                try:
                    response = load_balancer.proxy_request(port, endpoint, 'GET', **proxy_kwargs)
                    if response.status_code == 200:
                        data = response.json()
                        # 如果找到了任务信息，立即返回
                        if data.get('task_found', False) or data.get('progress', 0) > 0:
                            return Response(
                                response.content,
                                status=response.status_code,
                                headers=dict(response.headers)
                            )
                except Exception as e:
                    logger.debug(f"查询进度实例{port}失败: {e}")
                    continue
        
        # 如果所有实例都没有找到任务，返回最后一个健康实例的响应
        target_port = load_balancer.get_best_instance()
        response = load_balancer.proxy_request(target_port, endpoint, 'GET', **proxy_kwargs)
        
        return Response(
            response.content,
            status=response.status_code,
            headers=dict(response.headers)
        )
        
    except Exception as e:
        logger.error(f"❌ 检查处理进度代理失败: {e}")
        return jsonify({'error': 'Processing progress service temporarily unavailable'}), 503

if __name__ == '__main__':
    logger.info("🚀 启动RAG ColPali Manager智能负载均衡器")
    logger.info("📍 服务地址: http://10.98.64.22:5100")
    logger.info("📊 管理实例: 8个远程GPU实例")
    logger.info("🎯 负载均衡策略: 智能响应时间 + 请求计数 + 向量数据库状态")
    
    print("🚀 启动RAG ColPali Manager智能负载均衡器")
    print("📍 服务地址: http://10.98.64.22:5100") 
    print("📊 管理实例: 8个远程GPU实例")
    
    app.run(host='0.0.0.0', port=5100, threaded=True)