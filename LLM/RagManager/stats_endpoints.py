"""
为现有RAG Manager添加负载统计接口
这个补丁文件包含需要添加到colpali_rag_manager.py的代码
"""

# 添加到colpali_rag_manager.py中的API路由

@app.route('/stats', methods=['GET'])
def get_instance_stats():
    """获取实例统计信息，供负载均衡器使用"""
    try:
        # 统计当前活跃任务数
        active_tasks = len([task for task in task_manager._tasks.values() 
                           if task.status in ['pending', 'processing']])
        
        # 统计总处理任务数
        total_processed = len([task for task in task_manager._tasks.values() 
                             if task.status == 'completed'])
        
        # 获取RAG实例状态
        rag_status = "loaded" if rag_instance else "not_loaded"
        
        # 计算内存使用情况
        import psutil
        process = psutil.Process()
        memory_info = process.memory_info()
        
        stats = {
            'timestamp': time.time(),
            'instance_id': INSTANCE_ID,
            'gpu_id': GPU_ID,
            'port': RAG_MANAGER_PORT,
            'active_tasks': active_tasks,
            'total_processed': total_processed,
            'rag_status': rag_status,
            'memory_usage_mb': round(memory_info.rss / 1024 / 1024, 2),
            'uptime_seconds': time.time() - startup_time if 'startup_time' in globals() else 0,
            'health': 'healthy'
        }
        
        return jsonify(stats)
        
    except Exception as e:
        logger.error(f"获取实例统计信息失败: {str(e)}")
        return jsonify({
            'error': str(e),
            'timestamp': time.time(),
            'instance_id': INSTANCE_ID,
            'health': 'degraded'
        }), 500

@app.route('/load_info', methods=['GET'])
def get_load_info():
    """获取详细的负载信息"""
    try:
        # 获取任务统计信息
        task_stats = task_manager.get_task_statistics()
        
        # 获取配置信息
        config_count = len(rag_configurations)
        
        # 系统资源信息
        import psutil
        cpu_percent = psutil.cpu_percent(interval=1)
        memory = psutil.virtual_memory()
        
        load_info = {
            'timestamp': time.time(),
            'instance_info': {
                'id': INSTANCE_ID,
                'gpu_id': GPU_ID,
                'port': RAG_MANAGER_PORT
            },
            'task_statistics': task_stats,
            'config_count': config_count,
            'system_resources': {
                'cpu_percent': cpu_percent,
                'memory_percent': memory.percent,
                'memory_available_gb': round(memory.available / 1024**3, 2)
            },
            'rag_instance_loaded': rag_instance is not None
        }
        
        return jsonify(load_info)
        
    except Exception as e:
        logger.error(f"获取负载信息失败: {str(e)}")
        return jsonify({'error': str(e)}), 500