"""
API处理器模块 - 统一处理API请求和响应
"""
import os
import time
import hashlib
import logging
import traceback
from typing import Dict, Any, Optional, Tuple
from flask import request, jsonify

logger = logging.getLogger("ColPali-RAG-Manager")

class APIHandler:
    """API请求处理器，统一处理用户验证和错误处理"""
    
    def __init__(self, user_manager, config_manager):
        self.user_manager = user_manager
        self.config_manager = config_manager
    
    def extract_user_and_config(self, req=None, form_data=False) -> Tuple[str, str]:
        """从请求中提取用户ID和配置ID，改进用户识别逻辑"""
        if req is None:
            req = request
            
        if form_data:
            user_id = req.form.get('user_id')
            config_id = req.form.get('config_id', 'default')
        else:
            if req.method == 'GET':
                user_id = req.args.get('user_id')
                config_id = req.args.get('config_id', 'default')
            else:
                data = req.get_json() or {}
                user_id = data.get('user_id')
                config_id = data.get('config_id', 'default')
        
        # 改进用户ID处理逻辑
        if not user_id or user_id == 'default' or user_id == 'null':
            # 使用更合理的默认值
            user_id = 'anonymous'
        
        # 记录用户识别情况（调试用）
        logger.debug(f"提取用户信息: user_id={user_id}, config_id={config_id}, method={req.method}")
        
        return user_id, config_id
    
    def validate_config_access(self, user_id: str, config_id: str, configs: Dict) -> Tuple[bool, Optional[str], Optional[Dict]]:
        """验证用户对配置的访问权限"""
        if config_id not in configs:
            return False, f'配置ID无效: {config_id}', None
            
        config = configs[config_id]
        
        if not self.user_manager.can_user_access_config(user_id, config, config_id):
            return False, '无权限访问此配置', None
            
        return True, None, config
    
    def validate_config_modification(self, user_id: str, config_id: str, configs: Dict) -> Tuple[bool, Optional[str]]:
        """验证用户对配置的修改权限"""
        valid, error, config = self.validate_config_access(user_id, config_id, configs)
        if not valid:
            return False, error
            
        if not self.user_manager.can_user_modify_config(user_id, config_id):
            return False, f'配置 {config_id} 为只读，不允许修改'
            
        return True, None
    
    def create_error_response(self, message: str, status_code: int = 400) -> Tuple[Dict[str, Any], int]:
        """创建标准错误响应"""
        return jsonify({'error': message}), status_code
    
    def create_success_response(self, data: Dict[str, Any], status_code: int = 200) -> Tuple[Dict[str, Any], int]:
        """创建标准成功响应"""
        return jsonify(data), status_code
    
    def handle_api_error(self, endpoint: str, error: Exception):
        """统一处理API错误"""
        error_msg = str(error)
        logger.error(f'{endpoint}错误: {error_msg}')
        traceback.print_exc()
        return jsonify({'error': f'{endpoint}失败: {error_msg}'}), 500