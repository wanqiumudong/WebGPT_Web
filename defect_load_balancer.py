#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Defect 服务智能负载均衡器
管理8个远程GPU实例的负载分发
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
import io

# 配置日志
logging.basicConfig(level=logging.INFO, 
                   format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("Defect-LoadBalancer")

app = Flask(__name__)
CORS(app)

class DefectLoadBalancer:
    def __init__(self):
        # 远程A100服务器配置
        self.remote_server = "10.98.193.46"  # 恢复原始内网IP
        # self.defect_ports = [5008, 5018, 5028, 5038, 5048, 5058, 5068, 5078]
        self.defect_ports = [5008]
        
        # 实例状态管理
        self.instance_status = {}
        self.last_health_check = {}
        self.health_check_interval = 5  # 5秒检查间隔
        self.request_timeout = 10  # 10秒请求超时
        
        # 负载均衡策略
        self.current_instance = 0
        self.request_counts = {port: 0 for port in self.defect_ports}
        
        # 会话粘性：同一会话总是路由到相同GPU实例
        self.session_to_gpu = {}  # {session_key: port}
        
        # 线程池
        self.executor = ThreadPoolExecutor(max_workers=8)
        
        # 启动健康检查
        self.start_health_check()
        
        logger.info(f"✅ Defect负载均衡器初始化完成")
        logger.info(f"📍 管理实例: {len(self.defect_ports)}个")
        logger.info(f"🔗 端口范围: {min(self.defect_ports)}-{max(self.defect_ports)}")
        logger.info(f"🔒 支持会话粘性路由")
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
                        'response_time': response.elapsed.total_seconds()
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
                  for port in self.defect_ports]
        
        # 等待所有检查完成
        for future in futures:
            try:
                future.result(timeout=5)
            except Exception as e:
                logger.error(f"健康检查异常: {e}")
    
    def get_best_instance_for_session(self, session_key):
        """为会话选择最佳实例 - 支持会话粘性"""
        # 如果会话已经有绑定的GPU实例，优先使用
        if session_key in self.session_to_gpu:
            bound_port = self.session_to_gpu[session_key]
            # 检查绑定的实例是否健康
            if self.instance_status.get(bound_port, {}).get('healthy', False):
                logger.info(f"🔒 使用会话绑定实例: {session_key} -> GPU-{self.instance_status[bound_port].get('gpu_id', '?')} (端口{bound_port})")
                return bound_port
            else:
                # 绑定的实例不健康，移除绑定
                logger.warning(f"⚠️ 会话绑定实例不健康，重新选择: {session_key}")
                del self.session_to_gpu[session_key]
        
        # 选择新的最佳实例
        best_port = self.get_best_instance()
        
        # 建立会话绑定
        self.session_to_gpu[session_key] = best_port
        logger.info(f"🔗 建立会话绑定: {session_key} -> GPU-{self.instance_status.get(best_port, {}).get('gpu_id', '?')} (端口{best_port})")
        
        return best_port
    
    def get_best_instance(self):
        available_instances = []
        
        # 筛选健康且不忙碌的实例
        for port in self.defect_ports:
            status = self.instance_status.get(port, {})
            if status.get('healthy', False) and not status.get('busy', True):
                available_instances.append({
                    'port': port,
                    'response_time': status.get('response_time', 1.0),
                    'request_count': self.request_counts[port]
                })
        
        if not available_instances:
            # 如果没有可用实例，选择最少请求的健康实例
            healthy_instances = [port for port in self.defect_ports 
                               if self.instance_status.get(port, {}).get('healthy', False)]
            
            if healthy_instances:
                # 选择请求数最少的实例
                best_port = min(healthy_instances, key=lambda p: self.request_counts[p])
                logger.warning(f"⚠️ 所有实例繁忙，选择最少负载实例: {best_port}")
                return best_port
            else:
                # 所有实例都不健康，使用轮询策略
                self.current_instance = (self.current_instance + 1) % len(self.defect_ports)
                selected_port = self.defect_ports[self.current_instance]
                logger.warning(f"⚠️ 所有实例不健康，使用轮询策略: {selected_port}")
                return selected_port
        
        # 选择响应时间最短且请求数较少的实例
        best_instance = min(available_instances, 
                          key=lambda x: (x['response_time'] * 0.7 + x['request_count'] * 0.3))
        
        selected_port = best_instance['port']
        logger.info(f"🎯 选择最佳实例: GPU-{self.instance_status[selected_port].get('gpu_id', '?')} (端口{selected_port})")
        return selected_port
    
    def proxy_request(self, target_port, endpoint, method='POST', **kwargs):
        """代理请求到目标实例"""
        target_url = f"http://{self.remote_server}:{target_port}{endpoint}"
        
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
            
            logger.info(f"✅ 请求成功代理: {endpoint} -> GPU-{self.instance_status.get(target_port, {}).get('gpu_id', '?')}")
            return response
            
        except requests.exceptions.Timeout:
            logger.error(f"⏰ 请求超时: {target_url}")
            raise
        except requests.exceptions.RequestException as e:
            logger.error(f"❌ 请求失败: {target_url}, 错误: {e}")
            raise
    
    def get_cluster_stats(self):
        """获取集群统计信息"""
        stats = {
            'total_instances': len(self.defect_ports),
            'healthy_instances': 0,
            'busy_instances': 0,
            'total_requests': sum(self.request_counts.values()),
            'instances': []
        }
        
        for port in self.defect_ports:
            status = self.instance_status.get(port, {})
            instance_info = {
                'port': port,
                'gpu_id': status.get('gpu_id', -1),
                'healthy': status.get('healthy', False),
                'busy': status.get('busy', True),
                'request_count': self.request_counts[port],
                'response_time': status.get('response_time', 0),
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
load_balancer = DefectLoadBalancer()

@app.route('/health', methods=['GET'])
def health_check():
    """负载均衡器健康检查"""
    return jsonify({
        'status': 'healthy',
        'service': 'defect-load-balancer',
        'timestamp': time.time(),
        'managed_instances': len(load_balancer.defect_ports)
    })

@app.route('/status', methods=['GET'])  
def get_status():
    """获取集群状态"""
    return jsonify(load_balancer.get_cluster_stats())

@app.route('/predict', methods=['POST'])
def proxy_predict():
    """代理预测请求到最佳实例 - 常规负载均衡"""
    try:
        # 选择最佳实例（不使用会话粘性）
        target_port = load_balancer.get_best_instance()
        target_url = f"http://{load_balancer.remote_server}:{target_port}/predict"
        
        logger.info(f"🎯 选择实例: GPU-{load_balancer.instance_status.get(target_port, {}).get('gpu_id', '?')} (端口{target_port})")
        
        # 构建请求参数
        kwargs = {'timeout': 60}
        
        # 处理headers - 完全复制除了host相关的
        headers = {}
        for key, value in request.headers:
            if key.lower() not in ['host']:
                headers[key] = value
        kwargs['headers'] = headers
        
        # 处理请求体
        if hasattr(request, 'files') and request.files:
            # multipart/form-data 请求
            files = {}
            data = {}
            
            # 处理文件
            for key, file_storage in request.files.items():
                file_storage.seek(0)
                files[key] = (
                    file_storage.filename,
                    file_storage.read(),
                    file_storage.content_type or 'application/octet-stream'
                )
                logger.info(f"📎 处理文件: {key} = {file_storage.filename}")
            
            # 处理表单数据
            for key, value in request.form.items():
                data[key] = value
                logger.info(f"📝 处理表单: {key} = {value}")
            
            kwargs['files'] = files
            kwargs['data'] = data
            
            # 移除content-type让requests自动设置
            if 'content-type' in kwargs['headers']:
                del kwargs['headers']['content-type']
            if 'Content-Type' in kwargs['headers']:
                del kwargs['headers']['Content-Type']
                
        else:
            # 其他类型的请求
            kwargs['data'] = request.get_data()
        
        logger.info(f"🌐 转发到: {target_url}")
        
        # 发送请求
        response = requests.post(target_url, **kwargs)
        
        logger.info(f"✅ 请求转发完成: 状态{response.status_code}")
        load_balancer.request_counts[target_port] += 1
        
        # 构建响应
        def generate():
            return response.content
        
        return Response(
            generate(),
            status=response.status_code,
            headers=[(k, v) for k, v in response.headers.items() 
                    if k.lower() not in ['content-length', 'transfer-encoding', 'connection']]
        )
        
    except Exception as e:
        logger.error(f"❌ 代理请求失败: {e}")
        import traceback
        logger.error(f"错误详情: {traceback.format_exc()}")
        return jsonify({'error': 'Service temporarily unavailable', 'details': str(e)}), 503

@app.route('/uploadImage', methods=['POST'])
def proxy_upload_image():
    """代理图像上传请求"""
    return proxy_predict()  # 使用相同的逻辑

@app.route('/uploadMessage', methods=['POST'])  
def proxy_upload_message():
    """代理消息上传请求"""
    try:
        target_port = load_balancer.get_best_instance()
        
        # 转发原始请求体
        proxy_kwargs = {
            'data': request.data,
            'headers': {key: value for key, value in request.headers 
                       if key.lower() not in ['host', 'content-length', 'connection']}
        }
        
        response = load_balancer.proxy_request(target_port, '/uploadMessage', 'POST', **proxy_kwargs)
        
        return Response(
            response.content,
            status=response.status_code, 
            headers=dict(response.headers)
        )
        
    except Exception as e:
        logger.error(f"❌ 消息代理失败: {e}")
        return jsonify({'error': 'Service temporarily unavailable'}), 503

if __name__ == '__main__':
    logger.info("🚀 启动Defect智能负载均衡器")
    logger.info("📍 服务地址: http://10.98.64.22:5002")
    logger.info("📊 管理实例: 8个远程GPU实例")
    logger.info("🎯 负载均衡策略: 智能响应时间 + 请求计数")
    
    print("🚀 启动Defect智能负载均衡器")
    print("📍 服务地址: http://10.98.64.22:5002") 
    print("📊 管理实例: 8个远程GPU实例")
    
    app.run(host='0.0.0.0', port=5101, threaded=True)