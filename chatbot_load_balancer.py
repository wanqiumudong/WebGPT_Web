#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Chatbot 服务智能负载均衡器
管理8个本地Qwen2聊天实例的负载分发
"""

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
logger = logging.getLogger("Chatbot-LoadBalancer")

app = Flask(__name__)
CORS(app)

class ChatbotLoadBalancer:
    def __init__(self):
        # 本地服务器配置
        self.local_server = "10.98.64.22"
        # self.chatbot_ports = [5003, 5013, 5023, 5033, 5043, 5053, 5063, 5073]
        self.chatbot_ports = [5013]
        
        # 实例状态管理
        self.instance_status = {}
        self.last_health_check = {}
        self.health_check_interval = 5  # 5秒检查间隔
        self.request_timeout = 120  # 120秒请求超时 (流式聊天可能较慢)
        
        # 负载均衡策略
        self.current_instance = 0
        self.request_counts = {port: 0 for port in self.chatbot_ports}
        
        # 线程池
        self.executor = ThreadPoolExecutor(max_workers=8)
        
        # 启动健康检查
        self.start_health_check()
        
        logger.info(f"✅ Chatbot负载均衡器初始化完成")
        logger.info(f"📍 管理实例: {len(self.chatbot_ports)}个")
        logger.info(f"🔗 端口范围: {min(self.chatbot_ports)}-{max(self.chatbot_ports)}")
        
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
                url = f"http://{self.local_server}:{port}/health"
                response = requests.get(url, timeout=3)
                
                if response.status_code == 200:
                    status_data = response.json()
                    self.instance_status[port] = {
                        'healthy': True,
                        'busy': status_data.get('busy', False),
                        'instance_id': status_data.get('instance_id', -1),
                        'last_request_time': status_data.get('last_request_time', 0),
                        'total_requests': status_data.get('total_requests', 0),
                        'response_time': response.elapsed.total_seconds(),
                        'api_status': status_data.get('api_status', 'unknown')
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
                  for port in self.chatbot_ports]
        
        # 等待所有检查完成
        for future in futures:
            try:
                future.result(timeout=5)
            except Exception as e:
                logger.error(f"Chatbot健康检查异常: {e}")
    
    def get_best_instance(self):
        """智能选择最佳Chatbot实例"""
        available_instances = []
        
        # 筛选健康且不忙碌的实例
        for port in self.chatbot_ports:
            status = self.instance_status.get(port, {})
            if status.get('healthy', False) and not status.get('busy', True):
                available_instances.append({
                    'port': port,
                    'response_time': status.get('response_time', 1.0),
                    'request_count': self.request_counts[port],
                    'api_status': status.get('api_status', 'unknown')
                })
        
        if not available_instances:
            # 如果没有可用实例，选择最少请求的健康实例
            healthy_instances = [port for port in self.chatbot_ports 
                               if self.instance_status.get(port, {}).get('healthy', False)]
            
            if healthy_instances:
                # 选择请求数最少的实例
                best_port = min(healthy_instances, key=lambda p: self.request_counts[p])
                logger.warning(f"⚠️ 所有Chatbot实例繁忙，选择最少负载实例: {best_port}")
                return best_port
            else:
                # 所有实例都不健康，使用轮询策略
                self.current_instance = (self.current_instance + 1) % len(self.chatbot_ports)
                selected_port = self.chatbot_ports[self.current_instance]
                logger.warning(f"⚠️ 所有Chatbot实例不健康，使用轮询策略: {selected_port}")
                return selected_port
        
        # 选择响应时间最短且请求数较少的实例
        best_instance = min(available_instances, 
                          key=lambda x: (x['response_time'] * 0.5 + x['request_count'] * 0.5))
        
        selected_port = best_instance['port']
        logger.info(f"🎯 选择最佳Chatbot实例: ID-{self.instance_status[selected_port].get('instance_id', '?')} (端口{selected_port})")
        return selected_port
    
    def proxy_request(self, target_port, endpoint, method='POST', **kwargs):
        """代理请求到目标实例"""
        target_url = f"http://{self.local_server}:{target_port}{endpoint}"
        
        try:
            # 记录请求
            self.request_counts[target_port] += 1
            
            # 转发请求
            if method == 'POST':
                response = requests.post(target_url, timeout=self.request_timeout, **kwargs)
            elif method == 'GET':
                response = requests.get(target_url, timeout=self.request_timeout, **kwargs)
            else:
                response = requests.request(method, target_url, timeout=self.request_timeout, **kwargs)
            
            logger.info(f"✅ Chatbot请求成功代理: {endpoint} -> 实例-{self.instance_status.get(target_port, {}).get('instance_id', '?')}")
            return response
            
        except requests.exceptions.Timeout:
            logger.error(f"⏰ Chatbot请求超时: {target_url}")
            raise
        except requests.exceptions.RequestException as e:
            logger.error(f"❌ Chatbot请求失败: {target_url}, 错误: {e}")
            raise
    
    def get_cluster_stats(self):
        """获取Chatbot集群统计信息"""
        stats = {
            'total_instances': len(self.chatbot_ports),
            'healthy_instances': 0,
            'busy_instances': 0,
            'total_requests': sum(self.request_counts.values()),
            'instances': []
        }
        
        for port in self.chatbot_ports:
            status = self.instance_status.get(port, {})
            instance_info = {
                'port': port,
                'instance_id': status.get('instance_id', -1),
                'healthy': status.get('healthy', False),
                'busy': status.get('busy', True),
                'request_count': self.request_counts[port],
                'response_time': status.get('response_time', 0),
                'api_status': status.get('api_status', 'unknown'),
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
load_balancer = ChatbotLoadBalancer()

@app.route('/health', methods=['GET'])
def health_check():
    """负载均衡器健康检查"""
    return jsonify({
        'status': 'healthy',
        'service': 'chatbot-load-balancer',
        'timestamp': time.time(),
        'managed_instances': len(load_balancer.chatbot_ports)
    })

@app.route('/status', methods=['GET'])  
def get_status():
    """获取Chatbot集群状态"""
    return jsonify(load_balancer.get_cluster_stats())

@app.route('/generate', methods=['POST'])
def proxy_generate():
    """代理Chatbot生成请求到最佳实例"""
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
        logger.error(f"❌ Chatbot代理请求失败: {e}")
        return jsonify({'error': 'Chatbot service temporarily unavailable'}), 503

@app.route('/stream_generate', methods=['POST'])  
def proxy_stream_generate():
    """代理流式Chatbot生成请求"""
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
        logger.error(f"❌ Chatbot流式代理失败: {e}")
        return jsonify({'error': 'Chatbot streaming service temporarily unavailable'}), 503

@app.route('/abort_stream', methods=['POST'])
def proxy_abort_stream():
    """代理中止流式请求"""
    try:
        # 这里可以实现更复杂的路由逻辑，暂时使用简单轮询
        target_port = load_balancer.chatbot_ports[load_balancer.current_instance]
        
        proxy_kwargs = {
            'json' if request.is_json else 'data': request.get_json() if request.is_json else request.data,
            'headers': {key: value for key, value in request.headers 
                       if key.lower() not in ['host', 'content-length', 'connection']}
        }
        
        response = load_balancer.proxy_request(target_port, '/abort_stream', 'POST', **proxy_kwargs)
        
        return Response(
            response.content,
            status=response.status_code,
            headers=dict(response.headers)
        )
        
    except Exception as e:
        logger.error(f"❌ Chatbot中止代理失败: {e}")
        return jsonify({'error': 'Chatbot abort service temporarily unavailable'}), 503

if __name__ == '__main__':
    logger.info("🚀 启动Chatbot智能负载均衡器")
    logger.info("📍 服务地址: http://10.98.64.22:5008")
    logger.info("📊 管理实例: 8个本地Qwen2聊天实例")
    logger.info("🎯 负载均衡策略: 智能响应时间 + 请求计数 + API状态")
    
    print("🚀 启动Chatbot智能负载均衡器")
    print("📍 服务地址: http://10.98.64.22:5008") 
    print("📊 管理实例: 8个本地Qwen2聊天实例")
    
    app.run(host='0.0.0.0', port=5104, threaded=True)