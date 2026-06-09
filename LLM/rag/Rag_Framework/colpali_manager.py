import os
import time
import hashlib
import logging
import traceback
import torch
import numpy as np
from PIL import Image
from tqdm import tqdm
from pathlib import Path
from pdf2image import convert_from_path
from torch.utils.data import DataLoader, Dataset
from pymilvus import MilvusClient
from contextlib import nullcontext
from Rag_Framework.config_manager import MILVUS_URI, is_milvus_available

# 用于添加代码 -> 图片信息
from PIL import Image, ImageDraw, ImageFont
import textwrap

from colpali_engine.models import ColPali
from colpali_engine.models.paligemma.colpali.processing_colpali import ColPaliProcessor
from typing import List, cast
from pdfminer.high_level import extract_text
from pdfminer.layout import LAParams
import concurrent.futures

logger = logging.getLogger("ColPali-RAG-Manager")
RAG_ROOT = Path(__file__).resolve().parents[1]

class ColPaliManager:
    """ColPali模型和资源管理类，优化PDF文本提取逻辑"""
    
    def __init__(self, model_path="./models/vidore/colpali-v1.3", base_model_path=None, device="cuda:0"):
        """初始化ColPali模型管理器"""
        self.model_path = model_path
        self.base_model_path = base_model_path
        self.device = device
        self.model = None
        self.processor = None
        self.retriever = None
        self.config_dir = None
        self.documents = {}  # 存储文档信息的字典
        self.milvus_collection = None  # 当前使用的Milvus集合名称

        # 对rag_configurations的引用
        from Rag_Framework.config_manager import rag_configurations
        self.rag_configurations = rag_configurations

    def load_model(self):
        """加载ColPali模型和处理器"""
        try:
            # 设置Hugging Face镜像站点
            os.environ['HF_ENDPOINT'] = 'https://hf-mirror.com'
            
            # 配置设备
            device = torch.device(self.device if torch.cuda.is_available() else "cpu")
            logger.info(f"使用设备: {device}")
            
            # 定义模型路径
            models_root = Path(os.environ.get("WEB_FABGPT_RAG_MODELS_DIR", str(RAG_ROOT / "models")))
            base_model_path = self.base_model_path or str(models_root / "colpali" / "paligemma-3b-mix-448")
            _adapter_model_path = self.model_path or str(models_root / "colpali" / "colpali-v1.3")
            
            logger.info(f"使用基础模型路径: {base_model_path}")
            logger.info(f"使用adapter模型路径: {_adapter_model_path}")
            
            # 检查模型路径是否存在
            if not os.path.exists(base_model_path):
                logger.error(f"基础模型路径不存在: {base_model_path}")
                return False
                
            if _adapter_model_path and not os.path.exists(_adapter_model_path):
                logger.error(f"adapter模型路径不存在: {_adapter_model_path}")
                return False
            
            # 尝试加载模型
            try:
                # 步骤1：加载基础模型
                logger.info("正在加载基础模型...")
                # 检查GPU是否支持bfloat16或fp16
                dtype = torch.bfloat16
                if torch.cuda.is_available():
                    if not torch.cuda.is_bf16_supported():
                        dtype = torch.float16
                        logger.info("GPU不支持bfloat16，使用float16代替")
                    else:
                        logger.info("使用bfloat16精度加速")

                self.model = ColPali.from_pretrained(
                    base_model_path,
                    torch_dtype=dtype,
                    device_map=device
                ).eval()
                
                # 步骤2：如果提供了adapter路径，加载adapter
                if _adapter_model_path is not None:
                    logger.info(f"正在加载adapter: {_adapter_model_path}")
                    adapter_name = "colbert_adapter"  # 适当的adapter名称
                    token = None                      # 如需token可以在此设置
                    adapter_kwargs = {}               # 其他adapter参数
                    
                    self.model.load_adapter(
                        _adapter_model_path,
                        adapter_name=adapter_name,
                        token=token,
                        adapter_kwargs=adapter_kwargs,
                    )
                
                # 步骤3：加载处理器，使用adapter路径
                logger.info("正在加载处理器...")
                self.processor = cast(ColPaliProcessor, ColPaliProcessor.from_pretrained(
                    _adapter_model_path,  # 处理器应该从adapter路径加载
                    use_fast=True
                ))
                
                logger.info("模型、处理器加载成功")
                return True
                
            except Exception as e:
                logger.error(f"加载模型失败: {str(e)}")
                traceback.print_exc()
                    
        except Exception as e:
            logger.error(f"加载ColPali模型失败: {str(e)}")
            traceback.print_exc()
            return False

    def _sync_documents_standalone(self, collection_name):
        """为Milvus Standalone专门优化的文档同步方法"""
        try:
            logger.info("开始执行Standalone优化同步")
            sync_start_time = time.time()
            
            # 设置同步超时时间 - 防止过长同步
            max_sync_time = 30  # 最多30秒
        
            # 首先检查集合是否为空
            try:
                stats = self.retriever.client.get_collection_stats(collection_name)
                row_count = stats.get("row_count", 0)
                logger.info(f"集合中共有 {row_count} 行数据")
                
                # 如果集合为空，则直接返回成功，避免产生错误警告
                if row_count == 0:
                    logger.info(f"集合 '{collection_name}' 为空，无需同步文档")
                    self.documents = {}  # 重置为空文档集合
                    return True
                    
            except Exception as stats_err:
                logger.warning(f"获取集合统计信息失败: {str(stats_err)}")
                # 继续执行，尝试查询记录
            
            try:
                # 分批查询所有文档的第一条记录，避免遗漏
                unique_docs = []
                offset = 0
                batch_size = 1000
                max_batches = 100  # 最多查询100批，即10万条记录

                for batch in range(max_batches):
                    try:
                        batch_docs = self.retriever.client.query(
                            collection_name=collection_name,
                            filter="seq_id == 0",  # 只获取每个文档的第一条记录
                            output_fields=["doc_id", "doc", "text_content", "page_num", "image_path"],
                            offset=offset,
                            limit=batch_size
                        )
                        
                        if not batch_docs:
                            logger.info(f"第{batch+1}批查询结果为空，停止查询")
                            break
                            
                        unique_docs.extend(batch_docs)
                        logger.info(f"已查询第{batch+1}批，累计获取{len(unique_docs)}条记录")
                        
                        if len(batch_docs) < batch_size:
                            logger.info(f"最后一批记录数少于{batch_size}，查询完成")
                            break
                            
                        offset += batch_size
                        
                        # 防止单次同步时间过长
                        if time.time() - sync_start_time > max_sync_time:
                            logger.warning(f"同步时间超过{max_sync_time}秒，停止查询更多批次")
                            break
                            
                    except Exception as batch_err:
                        logger.error(f"查询第{batch+1}批时出错: {str(batch_err)}")
                        break

                logger.info(f"总计查询到 {len(unique_docs)} 条唯一文档记录")
            except Exception as query_err:
                logger.warning(f"查询失败: {str(query_err)}，尝试更简化的查询")
                # 尝试更简化的查询
                try:
                    unique_docs = self.retriever.client.query(
                        collection_name=collection_name,
                        filter="seq_id == 0",
                        output_fields=["doc_id", "doc"],
                        limit=5000
                    )
                except Exception as retry_err:
                    logger.error(f"简化查询也失败: {str(retry_err)}")
                    # 如果查询失败但我们已确认集合为空，仍然返回成功
                    if row_count == 0:
                        self.documents = {}
                        return True
                    return False
            
            # 如果查询结果为空，可能是真的没有文档
            if not unique_docs:
                logger.info("获取的唯一文档记录为空")
                self.documents = {}  # 重置为空文档集合
                return True
                
            logger.info(f"获取到 {len(unique_docs)} 条唯一文档记录，耗时: {time.time() - sync_start_time:.2f}秒")
            
            # 步骤2: 优化批处理逻辑 - 使用并行处理和超时控制
            docs_processed = 0
            valid_files = 0
            invalid_files = 0
            self.documents = {}  # 重置文档字典
            
            # 使用线程池并行处理文档
            from concurrent.futures import ThreadPoolExecutor
            
            def process_doc_record(record):
                try:
                    doc_id = record.get('doc_id')
                    file_path = record.get('doc', '')
                    
                    if not doc_id or not file_path:
                        return None
                    
                    # 确保我们使用标准化的路径，保持文档ID一致性
                    if os.path.exists(file_path):
                        file_path = os.path.abspath(file_path)
                    
                    # 如果路径存在，直接使用
                    valid_path = None
                    if os.path.exists(file_path):
                        valid_path = file_path
                    else:
                        # 尝试查找有效文件路径
                        # 1. 检查是否为相对路径，尝试基于知识库目录构建完整路径
                        if hasattr(self, 'config_dir') and self.config_dir:
                            for config_id, config in self.config_dir.items():
                                if 'folder' in config:
                                    base_dir = config['folder']
                                    possible_path = os.path.join(base_dir, os.path.basename(file_path))
                                    if os.path.exists(possible_path):
                                        valid_path = os.path.abspath(possible_path)
                                        logger.debug(f"找到有效路径: {file_path} -> {valid_path}")
                                        break
                    
                    # 如果找不到有效路径，返回None
                    if not valid_path:
                        logger.debug(f"无法找到有效文件路径: {file_path}")
                        return None
                    
                    # 确保我们使用标准化的路径，保持文档ID一致性
                    file_path = valid_path
                    
                    # 计算基础文档ID - 使用绝对路径确保一致性
                    abs_path = os.path.abspath(file_path)
                    hash_obj = hashlib.md5(abs_path.encode())
                    base_doc_id = int(hash_obj.hexdigest()[:8], 16)
                    
                    # 从记录中获取基本信息
                    text_content = record.get('text_content', '')
                    page_num = record.get('page_num', 0)
                    
                    # 估算页数 - 使用文件大小快速估算避免打开PDF
                    file_size = os.path.getsize(file_path)
                    page_count = max(1, file_size // (150 * 1024))  # 假设每页约150KB
                    
                    # 创建文档信息对象
                    last_modified = os.path.getmtime(file_path)
                    return (base_doc_id, self._create_document_info(
                        file_path, page_count, last_modified, text_content, True))
                except Exception as e:
                    logger.debug(f"处理文档记录失败: {str(e)}")
                    return None
            
            # 限制处理时间
            with ThreadPoolExecutor(max_workers=10) as executor:
                futures = [executor.submit(process_doc_record, record) for record in unique_docs]
                
                for future in futures:
                    if time.time() - sync_start_time > max_sync_time:
                        logger.warning(f"同步时间超过{max_sync_time}秒，提前结束处理")
                        break
                        
                    try:
                        result = future.result(timeout=0.5)  # 每条记录处理超时为0.5秒
                        if result:
                            base_doc_id, doc_info = result
                            self.documents[base_doc_id] = doc_info
                            docs_processed += 1
                            valid_files += 1
                        else:
                            invalid_files += 1
                    except Exception:
                        invalid_files += 1
                    
                    # 每100个文档记录一次进度
                    if (valid_files + invalid_files) % 100 == 0:
                        logger.info(f"已处理 {valid_files + invalid_files}/{len(unique_docs)} 个文档")
            
            # 添加更详细的日志
            logger.info(f"文档同步完成，成功处理 {docs_processed} 个文档，有效文件 {valid_files}，无效文件路径 {invalid_files}，总耗时: {time.time() - sync_start_time:.2f}秒")
            return True
                
        except Exception as e:
            logger.error(f"Standalone同步出错: {str(e)}")
            traceback.print_exc()
            return False
    
    def extract_text_from_pdf_page(self, pdf_path, page_number):
        """使用pdfminer.six从PDF指定页面提取文本"""
        try:
            # 配置LAParams以优化文本提取
            laparams = LAParams(
                boxes_flow=0.5,
                word_margin=0.1,
                char_margin=2.0,
                line_margin=0.5,
                detect_vertical=False
            )
            
            logger.debug(f"开始从PDF提取第{page_number}页文本: {pdf_path}")
            
            # 提取指定页面的文本 (pdfminer使用0基索引)
            text = extract_text(
                pdf_path, 
                page_numbers=[page_number-1], 
                laparams=laparams,
                codec='utf-8'
            )
            
            # 清理和标准化文本
            if text:
                text = text.strip()
                # 移除过多的空白字符
                import re
                text = re.sub(r'\s+', ' ', text)
                text = re.sub(r'\n\s*\n', '\n', text)
                
            logger.debug(f"从PDF第{page_number}页提取文本成功: {len(text) if text else 0}字符")
            return text if text else ""
            
        except Exception as e:
            logger.error(f"从PDF第{page_number}页提取文本失败: {str(e)}")
            traceback.print_exc()
            return ""

    def extract_text_from_pdf_parallel(self, pdf_path, total_pages):
        """并行提取PDF所有页面的文本"""
        try:
            logger.info(f"开始并行提取PDF文本: {pdf_path}, 共{total_pages}页")
            page_texts = {}
            
            # 使用线程池并行处理
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, total_pages)) as executor:
                # 提交所有页面的提取任务
                future_to_page = {
                    executor.submit(self.extract_text_from_pdf_page, pdf_path, page_num): page_num
                    for page_num in range(1, total_pages + 1)
                }
                
                # 收集结果
                for future in concurrent.futures.as_completed(future_to_page):
                    page_num = future_to_page[future]
                    try:
                        text = future.result(timeout=10)  # 每页最多10秒
                        page_texts[page_num] = text
                    except Exception as e:
                        logger.error(f"提取第{page_num}页文本失败: {str(e)}")
                        page_texts[page_num] = ""
            
            logger.info(f"并行文本提取完成，成功提取{len([t for t in page_texts.values() if t])}页文本")
            return page_texts
            
        except Exception as e:
            logger.error(f"并行文本提取失败: {str(e)}")
            return {}

    # 添加同步文档信息的方法
    def sync_documents_from_milvus(self):
        """从Milvus同步文档信息"""
        if not self.retriever or not hasattr(self.retriever, 'client'):
            logger.error("Milvus检索器未初始化，无法同步文档")
            return False
        
        try:
            # 获取集合信息
            collection_name = self.retriever.collection_name
            logger.info(f"正在从集合 '{collection_name}' 同步文档信息")
            
            # 获取集合统计信息
            try:
                stats = self.retriever.client.get_collection_stats(collection_name)
                row_count = stats.get("row_count", 0)
                logger.info(f"集合中共有 {row_count} 行数据")
                
                # 如果集合为空，则直接返回成功，避免产生错误警告
                if row_count == 0:
                    logger.info(f"集合 '{collection_name}' 为空，无需同步文档")
                    self.documents = {}  # 重置为空文档集合
                    return True
                    
            except Exception as e:
                logger.warning(f"获取集合统计信息失败: {str(e)}")
                row_count = 1000000  # 假设数据量大
            
            # 同步
            if row_count > 100000:
                logger.info("记录数量较大，使用聚合查询模式")
                return self._sync_documents_aggregated(collection_name)
            else:
                logger.info("使用Standalone方法同步")
                return self._sync_documents_standalone(collection_name)
            
            # 使用分页批量查询获取文档ID
            page_size = 1000
            offset = 0
            max_pages = 20  # 最多获取20000条记录
            
            all_records = []
            processed_ids = set()
            
            for page in range(max_pages):
                try:
                    # 只查询seq_id=0的记录（文档元数据记录）
                    records = self.retriever.client.query(
                        collection_name=collection_name,
                        filter="seq_id == 0",  # 只获取每个文档的第一条记录
                        output_fields=["doc_id", "doc"],
                        offset=offset,
                        limit=page_size
                    )
                    
                    if not records:
                        logger.info(f"页面 {page+1} 没有返回记录，可能已获取所有文档")
                        break
                    
                    # 过滤已处理的ID
                    new_records = []
                    for record in records:
                        doc_id = record.get('doc_id')
                        if doc_id is not None and doc_id not in processed_ids:
                            new_records.append(record)
                            processed_ids.add(doc_id)
                    
                    all_records.extend(new_records)
                    logger.info(f"同步进度: 已获取 {len(all_records)} 条唯一文档记录")
                    
                    if len(records) < page_size:
                        # 没有更多记录了
                        break
                    
                    offset += page_size
                    
                except Exception as page_error:
                    logger.error(f"获取页面 {page+1} 数据时出错: {str(page_error)}")
                    break
            
            # 如果没有找到任何记录
            if not all_records:
                logger.warning("未找到任何文档记录")
                return False
            
            # 处理记录，构建文档字典
            doc_count = 0
            for record in all_records:
                doc_id = record.get('doc_id')
                file_path = record.get('doc', '')
                
                if doc_id and file_path and os.path.exists(file_path):
                    # 计算基础文档ID
                    base_doc_id = doc_id // 1000
                    
                    # 获取文件状态信息
                    try:
                        # 优化：使用文件大小估算页数，避免打开大型PDF
                        file_size = os.path.getsize(file_path)
                        page_count = max(1, file_size // (150 * 1024))  # 假设每页约150KB
                        
                        # 只对小文件执行真实页数检查
                        if file_size < 30 * 1024 * 1024 and file_path.lower().endswith('.pdf'):
                            try:
                                from PyPDF2 import PdfReader
                                reader = PdfReader(file_path)
                                page_count = len(reader.pages)
                            except Exception as pdf_err:
                                logger.debug(f"无法读取PDF页数: {str(pdf_err)}")
                        
                        # 创建文档信息对象
                        last_modified = os.path.getmtime(file_path)
                        self.documents[base_doc_id] = self._create_document_info(
                            file_path, page_count, last_modified, "", True)
                        
                        doc_count += 1
                        
                    except Exception as doc_err:
                        logger.debug(f"处理文档 {doc_id} 时出错: {str(doc_err)}")
            
            logger.info(f"文档同步完成，共获取 {doc_count} 个有效文档")
            return True
            
        except Exception as e:
            logger.error(f"从Milvus同步文档信息时出错: {str(e)}")
            traceback.print_exc()
            return False
            
    def _sync_documents_aggregated(self, collection_name):
        """使用聚合方式同步文档（处理大量记录的情况）"""
        try:
            # 验证集合名称
            import re
            if not re.match(r'^[a-zA-Z0-9_]+$', collection_name):
                logger.error(f"无效的集合名称: {collection_name}")
                return False

            logger.info(f"使用集合名称: {collection_name}")

            # 检查集合是否存在
            if not self.retriever.client.has_collection(collection_name):
                logger.warning(f"集合 {collection_name} 不存在")
                return False

            # 使用分组查询，仅获取每个 doc_id 的一条记录
            unique_records = []
            processed_ids = set()

            # 获取所有唯一的文档 ID
            basic_records = self.retriever.client.query(
                collection_name=collection_name,
                filter="seq_id == 0",
                output_fields=["doc_id", "doc"],
                limit=5000
            )

            if not basic_records:
                logger.warning("未找到文档记录")
                return False

            logger.info(f"获取到 {len(basic_records)} 条基础文档记录")

            # 处理记录
            for record in basic_records:
                doc_id = record.get('doc_id')
                if doc_id is not None and doc_id not in processed_ids:
                    unique_records.append(record)
                    processed_ids.add(doc_id)

            # 从唯一记录构建文档字典
            for record in unique_records:
                doc_id = record.get('doc_id')
                file_path = record.get('doc', '')

                if doc_id and file_path:
                    # 计算基础文档 ID
                    base_doc_id = doc_id // 1000

                    # 创建文档信息对象
                    if os.path.exists(file_path):
                        # 获取 PDF 页面数
                        page_count = 0
                        try:
                            if file_path.lower().endswith('.pdf'):
                                from PyPDF2 import PdfReader
                                reader = PdfReader(file_path)
                                page_count = len(reader.pages)
                        except Exception as pdf_err:
                            logger.warning(f"无法读取 PDF 页面数: {str(pdf_err)}")

                        # 创建文档信息对象
                        last_modified = os.path.getmtime(file_path)
                        self.documents[base_doc_id] = self._create_document_info(
                            file_path, page_count, last_modified, "", True)

            logger.info(f"成功同步 {len(self.documents)} 个文档信息")
            return True
        except Exception as e:
            logger.error(f"聚合文档同步失败: {str(e)}")
            traceback.print_exc()
            return False

    def _create_document_info(self, file_path, page_count, last_modified, text_content="", processed=True):
        """创建统一的文档信息对象"""
        class DocumentInfo:
            def __init__(self, file_path, page_count, last_modified, text_content="", processed=True):
                self.file_path = file_path
                self.page_count = page_count
                self.last_modified = last_modified
                self.text_content = text_content
                self.processed = processed
                self.file_exists = os.path.exists(file_path)
        
        return DocumentInfo(file_path, page_count, last_modified, text_content, processed)
    
    def set_current_collection(self, collection_name):
        """设置当前使用的Milvus集合"""
        if not self.retriever or not hasattr(self.retriever, 'client'):
            logger.error("Milvus检索器未初始化，无法设置集合")
            return False
            
        try:
            # 检查集合是否存在
            if not self.retriever.client.has_collection(collection_name):
                logger.info(f"集合 {collection_name} 不存在，尝试创建")
                
                # 创建新集合
                self.retriever.collection_name = collection_name
                self.retriever.create_collection()
                self.retriever.create_index()
                logger.info(f"已创建新集合: {collection_name}")
            else:
                # 切换到已有集合
                logger.info(f"切换到已有集合: {collection_name}")
                self.retriever.collection_name = collection_name
                self.retriever.client.load_collection(collection_name)
            
            # 更新集合名称
            self.milvus_collection = collection_name
            
            # 同步文档信息
            self.sync_documents_from_milvus()
            
            return True
        except Exception as e:
            logger.error(f"设置当前集合时出错: {str(e)}")
            traceback.print_exc()
            return False

    def delete_document_from_milvus(self, file_path):
        """从Milvus中删除文档"""
        if not self.retriever or not hasattr(self.retriever, 'client'):
            logger.error("Milvus检索器未初始化，无法删除文档")
            return False
        
        try:
            # 计算文档ID - 确保在INT64范围内
            hash_str = hashlib.md5(file_path.encode()).hexdigest()
            # 只取前8位十六进制数，转为整数，保持ID较小
            doc_id_int = int(hash_str[:8], 16)
            
            logger.info(f"删除文档，使用ID: {doc_id_int} (十六进制前8位: 0x{hash_str[:8]})")
            
            # 使用改进的删除方法
            delete_result = self.retriever.delete_document(doc_id_int)
        
        except Exception as e:
            logger.error(f"删除文档时出错: {str(e)}")
            traceback.print_exc()
            return {
                "success": False,
                "error": str(e)
            }

    def setup_milvus(self, collection_name):
        """设置Milvus客户端和检索器，显式使用Standalone模式 - 修复版本"""
        try:
            if not is_milvus_available(force=True):
                logger.warning("Milvus 当前不可达，跳过集合切换: %s", MILVUS_URI)
                return False

            # 导入MilvusColbertRetriever
            from Rag_Framework.milvus_retriever import MilvusColbertRetriever
            
            # 设置连接参数 - 使用标准Standalone连接参数
            client_params = {
                "uri": MILVUS_URI,
            }
            
            # 创建数据库名称 (每个知识库使用单独的数据库)
            db_name = f"rag_{collection_name}"
            
            logger.info(f"准备连接数据库: {db_name}, 集合: {collection_name}")
            
            # 创建客户端连接
            client = MilvusClient(**client_params)
            logger.info(f"已连接Milvus Standalone服务")
            
            # 关键修复：如果已有检索器且集合名称不同，先清理旧连接
            if hasattr(self, 'retriever') and self.retriever:
                old_collection = getattr(self.retriever, 'collection_name', None)
                if old_collection and old_collection != collection_name:
                    logger.info(f"检测到集合切换: {old_collection} -> {collection_name}, 清理旧连接")
                    try:
                        # 清理旧的客户端连接
                        if hasattr(self.retriever, 'client'):
                            del self.retriever.client
                    except Exception as cleanup_err:
                        logger.warning(f"清理旧连接时出错: {str(cleanup_err)}")
            
            # 确保数据库存在
            try:
                # 列出所有数据库
                all_dbs = client.list_databases()
                logger.info(f"现有数据库列表: {all_dbs}")
                
                # 创建数据库(如果不存在)
                if db_name not in all_dbs:
                    try:
                        client.create_database(db_name=db_name)
                        logger.info(f"已创建数据库: {db_name}")
                    except Exception as db_err:
                        logger.warning(f"创建数据库出错 (可能已存在): {str(db_err)}")
                else:
                    logger.info(f"数据库 {db_name} 已存在")
                
                # 切换到该数据库
                client.use_database(db_name=db_name)
                logger.info(f"已切换到数据库: {db_name}")
                
            except Exception as db_list_err:
                logger.error(f"数据库操作失败: {str(db_list_err)}")
                return False
            
            # 创建检索器
            self.retriever = MilvusColbertRetriever(
                milvus_client=client,
                collection_name=collection_name,
                dim=128  # ColPali的向量维度
            )
            
            # 检查集合是否存在
            collection_exists = client.has_collection(collection_name)
            
            if not collection_exists:
                logger.info(f"创建新集合: {collection_name}")
                # create_collection方法内部已经包含了创建索引和加载集合的操作
                success = self.retriever.create_collection()
                if not success:
                    logger.error(f"创建集合 {collection_name} 失败")
                    return False
            else:
                logger.info(f"使用已有集合: {collection_name}")
                try:
                    # 只在集合存在时加载一次
                    client.load_collection(collection_name)
                    logger.info(f"已加载集合 {collection_name}")
                except Exception as load_err:
                    logger.warning(f"加载集合出错: {str(load_err)}")
            
            # 记录检索器的集合名称
            self.milvus_collection = collection_name
            
            # 初始化空文档集合
            if not hasattr(self, 'documents'):
                self.documents = {}
            
            # 针对新集合的特殊处理
            if not collection_exists or client.get_collection_stats(collection_name).get("row_count", 0) == 0:
                logger.info(f"集合 {collection_name} 是新创建的或空的，无需同步文档")
                # 对于新集合，直接设置为空文档集合，避免不必要的同步警告
                self.documents = {}
                return True
                
            return True
        except Exception as e:
            logger.error(f"设置Milvus失败: {str(e)}")
            traceback.print_exc()
            return False

    def extract_text_from_pdf_by_image_path(self, image_path: str) -> dict:
        """
        根据图像路径反推PDF文件和页码，然后提取文本
        """
        try:
            # 从图像路径解析PDF文件路径和页码
            # 假设图像路径格式为: /path/to/rag_data/config_id/doc_id/page_X.png
            path_parts = image_path.split(os.sep)
            
            if len(path_parts) < 2:
                return {"text": "", "success": False, "error": "无法解析图像路径"}
                
            # 从文件名提取页码 (page_X.png)
            image_filename = os.path.basename(image_path)
            if not image_filename.startswith('page_') or not image_filename.endswith('.png'):
                return {"text": "", "success": False, "error": "图像文件名格式不正确"}
                
            try:
                page_number = int(image_filename.replace('page_', '').replace('.png', ''))
            except ValueError:
                return {"text": "", "success": False, "error": "无法解析页码"}
            
            # 需要找到对应的PDF文件
            # 方法1: 从documents字典中查找
            pdf_path = None
            doc_id_str = os.path.basename(os.path.dirname(image_path))
            
            try:
                doc_id = int(doc_id_str)
                if hasattr(self, 'documents') and doc_id in self.documents:
                    doc_info = self.documents[doc_id]
                    if hasattr(doc_info, 'file_path'):
                        pdf_path = doc_info.file_path
            except (ValueError, AttributeError):
                pass
                
            # 方法2: 如果找不到，尝试从Milvus查询
            if not pdf_path and hasattr(self, 'retriever'):
                try:
                    min_doc_id = int(doc_id_str) * 1000
                    max_doc_id = min_doc_id + 1000
                    
                    records = self.retriever.client.query(
                        collection_name=self.retriever.collection_name,
                        filter=f"doc_id >= {min_doc_id} AND doc_id < {max_doc_id} AND seq_id == 0",
                        output_fields=["filepath"],
                        limit=1
                    )
                    
                    if records:
                        pdf_path = records[0].get('filepath')
                except Exception as query_err:
                    logger.debug(f"从Milvus查询PDF路径失败: {str(query_err)}")
            
            if not pdf_path or not os.path.exists(pdf_path):
                return {"text": "", "success": False, "error": f"找不到对应的PDF文件: {pdf_path}"}
                
            # 使用pdfminer提取文本
            extracted_text = self.extract_text_from_pdf_page(pdf_path, page_number)
            
            if extracted_text:
                logger.info(f"成功从PDF提取文本: {pdf_path}, 第{page_number}页, 长度: {len(extracted_text)}")
                return {"text": extracted_text, "success": True}
            else:
                return {"text": "", "success": False, "message": "未能提取到文本内容"}
                
        except Exception as e:
            logger.error(f"根据图像路径提取PDF文本失败: {image_path}, 错误: {str(e)}")
            traceback.print_exc()
            return {"text": "", "success": False, "error": str(e)}

    def process_file(self, file_path, output_dir, doc_id=None, task_id=None, progress_callback=None, config=None):
        """处理PDF文件，只保存图像和向量，文本完全按需提取"""
        if not os.path.exists(file_path):
            logger.error(f"文件不存在: {file_path}")
            return False
                            
        if not self.model or not self.processor or not self.retriever:
            logger.error("模型或检索器未初始化")
            return False
        
        # 只允许PDF文件
        file_ext = os.path.splitext(file_path)[1].lower()
        if file_ext != '.pdf':
            logger.error(f"不支持的文件类型: {file_ext}，只支持PDF文件")
            return False
        
        try:
            # 生成文档ID - 确保使用整数类型，并在重新启动时保持一致
            if not doc_id:
                # 1. 始终使用标准化的绝对路径生成文档ID
                abs_path = os.path.abspath(file_path)
                # 2. 添加文件名作为备用标识
                file_name = os.path.basename(abs_path)
                # 3. 获取文件大小作为辅助指纹
                try:
                    file_size = os.path.getsize(abs_path)
                except:
                    file_size = 0
                
                # 4. 组合多个特征生成更稳定的ID
                id_string = f"{abs_path}|{file_name}|{file_size}"
                hash_obj = hashlib.md5(id_string.encode())
                # 5. 只取前8位确保是合理范围的整数
                doc_id_int = int(hash_obj.hexdigest()[:8], 16)
                
                logger.info(f"为文件生成稳定ID: {file_name} -> {doc_id_int} (0x{doc_id_int:x})")
            else:
                # 处理传入的doc_id，确保是整数
                if isinstance(doc_id, str):
                    # 如果是哈希字符串，标准化处理
                    if doc_id.startswith('0x'):
                        # 十六进制字符串
                        try:
                            doc_id_int = int(doc_id, 16) & 0xFFFFFFFF
                        except:
                            # 回退到标准哈希
                            hash_obj = hashlib.md5(doc_id.encode())
                            doc_id_int = int(hash_obj.hexdigest()[:8], 16)
                    else:
                        # 普通字符串
                        hash_obj = hashlib.md5(doc_id.encode())
                        doc_id_int = int(hash_obj.hexdigest()[:8], 16)
                else:
                    # 传入的已经是整数，确保在合理范围内
                    doc_id_int = int(doc_id) & 0xFFFFFFFF  # 限制为32位整数
                    
            # 记录使用的文档ID
            logger.info(f"使用文档ID: {doc_id_int} (十六进制: 0x{doc_id_int:x})")
            
            # 检查文档是否已处理过
            if hasattr(self, 'documents') and doc_id_int in self.documents:
                doc_info = self.documents[doc_id_int]
                if hasattr(doc_info, 'processed') and doc_info.processed:
                    logger.info(f"文档 {doc_id_int} 在内存中已标记为处理过，跳过处理")
                    return {
                        "doc_id": doc_id_int,
                        "file_path": file_path,
                        "already_processed": True,
                        "success": True,
                        "status": "processed",
                        "collection_name": self.retriever.collection_name
                    }

            # 如果内存中没有记录，检查Milvus中是否存在
            try:
                min_doc_id = doc_id_int * 1000
                max_doc_id = (doc_id_int + 1) * 1000
                existing_records = self.retriever.client.query(
                    collection_name=self.retriever.collection_name,
                    filter=f"doc_id >= {min_doc_id} AND doc_id < {max_doc_id}",
                    output_fields=["doc_id"],
                    limit=1
                )
                
                if existing_records:
                    logger.info(f"文档 {doc_id_int} 在Milvus中已存在，跳过处理")
                    # 创建一个简单的文档信息对象
                    self.documents[doc_id_int] = self._create_document_info(
                        file_path, 0, os.path.getmtime(file_path), "", True)
                    return {
                        "doc_id": doc_id_int,
                        "file_path": file_path,
                        "already_processed": True,
                        "success": True,
                        "status": "processed",
                        "collection_name": self.retriever.collection_name
                    }
            except Exception as check_err:
                logger.warning(f"检查Milvus中文档状态失败: {str(check_err)}")
            
            # 创建输出目录
            doc_output_dir = os.path.join(output_dir, str(doc_id_int))
            os.makedirs(doc_output_dir, exist_ok=True)
                
            # 1. 将PDF转换为图像
            logger.info(f"开始转换PDF: {file_path}")
            convert_pdf_start = time.time()
            images = convert_from_path(file_path)
            convert_pdf_time = time.time() - convert_pdf_start
            logger.info(f"PDF共 {len(images)} 页，转换耗时 {convert_pdf_time:.2f}秒")
            
            # 更新进度 - 使用回调函数
            if progress_callback and task_id:
                progress_callback(task_id, {
                    'current_step': 'converting_pdf',
                    'total_pages': len(images),
                    'progress': 40
                })
        
            # 保存页面图像
            page_paths = []
            save_pdf_start = time.time()
            for i, image in enumerate(images):
                page_path = os.path.join(doc_output_dir, f"page_{i + 1}.png")
                image.save(page_path, "PNG")
                page_paths.append(page_path)
                
                # 更新进度 - 使用回调函数
                if progress_callback and task_id and (i % 5 == 0 or i == len(images) - 1):
                    progress_callback(task_id, {
                        'current_step': 'saving_pages',
                        'current_page': i + 1,
                        'total_pages': len(images),
                        'progress': 40 + (i / len(images) * 15)  # 40-55%
                    })
                
                # 每100页记录一次进度
                if (i + 1) % 100 == 0 or i == 0 or i == len(images) - 1:
                    logger.info(f"保存PDF页面进度: {i + 1}/{len(images)}")
            
            save_pdf_time = time.time() - save_pdf_start
            logger.info(f"保存PDF页面耗时 {save_pdf_time:.2f}秒")

            # 2. 处理图像并生成嵌入向量
            if progress_callback and task_id:
                progress_callback(task_id, {
                    'current_step': 'generating_embeddings',
                    'progress': 55
                })
            logger.info("开始生成页面向量嵌入...")
            embedding_start = time.time()
            
            # 根据PDF大小优化处理策略
            total_pages = len(page_paths)
            logger.info(f"需要处理的总页数: {total_pages}")
            
            # 导入必要的模块
            from colpali_engine.utils.torch_utils import ListDataset
            
            # 优化批处理逻辑 - 对大文档使用分批处理
            if total_pages > 200:
                logger.info(f"检测到大型文档 ({total_pages}页)，使用分批处理模式")
                
                # 分批加载图像，避免一次性占用过多内存
                all_embeddings = []
                batch_size = 50  # 每批次处理50页
                batch_count = (total_pages + batch_size - 1) // batch_size  # 向上取整
                
                for batch_idx in range(batch_count):
                    start_idx = batch_idx * batch_size
                    end_idx = min(start_idx + batch_size, total_pages)
                    current_batch_paths = page_paths[start_idx:end_idx]
                    
                    # 更新进度 - 使用回调函数
                    if progress_callback and task_id:
                        batch_progress = batch_idx / batch_count
                        progress_callback(task_id, {
                            'progress': 55 + (batch_progress * 25),  # 映射到55-80%范围
                            'current_page': start_idx + 1,
                            'current_step': 'generating_embeddings',
                            'total_pages': total_pages
                        })
                    
                    logger.info(f"处理批次 {batch_idx+1}/{batch_count}，页面 {start_idx+1}-{end_idx}/{total_pages}")
                    
                    # 加载当前批次的图像
                    batch_images = [Image.open(path) for path in current_batch_paths]
                    
                    # 处理当前批次
                    batch_dataset = ListDataset[Image.Image](batch_images)
                    batch_dataloader = DataLoader(
                        dataset=batch_dataset,
                        batch_size=4,
                        shuffle=False,
                        collate_fn=lambda x: self.processor.process_images(x),
                    )
                    
                    # 生成嵌入向量
                    batch_embeddings = []
                    for sub_batch_idx, batch_doc in enumerate(batch_dataloader):
                        with torch.no_grad():
                            batch_doc = {k: v.to(self.model.device) for k, v in batch_doc.items()}
                            embeddings_doc = self.model(**batch_doc)
                            batch_embeddings.extend(list(torch.unbind(embeddings_doc.cpu())))
                    
                    # 将该批次的嵌入向量添加到结果中
                    all_embeddings.extend(batch_embeddings)
                    
                    # 释放内存
                    del batch_images, batch_dataset, batch_dataloader, batch_embeddings
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                    
                    # 添加日志以追踪进度
                    if (batch_idx + 1) % 5 == 0 or batch_idx == 0 or batch_idx == batch_count - 1:
                        logger.info(f"已完成 {batch_idx+1}/{batch_count} 批次的向量嵌入生成")
            else:
                # 对于小型文档，使用原有处理方式
                logger.info(f"使用标准处理模式 (共 {total_pages}页)")
                images = [Image.open(path) for path in page_paths]
                
                dataloader = DataLoader(
                    dataset=ListDataset[Image.Image](images),
                    batch_size=4,
                    shuffle=False,
                    collate_fn=lambda x: self.processor.process_images(x),
                )
                
                all_embeddings = []
                for batch_idx, batch_doc in enumerate(dataloader):
                    with torch.no_grad():
                        batch_doc = {k: v.to(self.model.device) for k, v in batch_doc.items()}
                        embeddings_doc = self.model(**batch_doc)
                        all_embeddings.extend(list(torch.unbind(embeddings_doc.cpu())))
                        
                    # 更新进度 - 使用回调函数
                    if progress_callback and task_id and (batch_idx % 5 == 0 or batch_idx == len(dataloader) - 1):
                        batch_progress = (batch_idx + 1) / len(dataloader)
                        progress_callback(task_id, {
                            'current_step': 'generating_embeddings',
                            'progress': 55 + (batch_progress * 25),
                            'current_page': (batch_idx + 1) * 4,  # 近似值
                            'total_pages': total_pages
                        })
                
                    if batch_idx % 10 == 0:  # 减少日志频率
                        logger.info(f"处理批次 {batch_idx + 1}/{len(dataloader)}")
            
            embedding_time = time.time() - embedding_start
            logger.info(f"生成嵌入向量耗时 {embedding_time:.2f}秒")
            
            # 3. 插入数据到Milvus - 关键：不包含任何文本内容
            logger.info(f"开始将数据插入Milvus集合 {self.retriever.collection_name}...")
            insert_data_start = time.time()
            success_count = 0
            
            # 更新进度 - 使用回调函数
            if progress_callback and task_id:
                progress_callback(task_id, {
                    'current_step': 'inserting_data',
                    'progress': 80
                })
            
            # 添加检查Milvus连接状态的逻辑
            if not hasattr(self.retriever, 'client') or not self.retriever.client:
                logger.error("Milvus客户端未初始化，无法插入数据")
                return False
                
            # 检查集合是否存在
            if not self.retriever.client.has_collection(self.retriever.collection_name):
                logger.info(f"集合 {self.retriever.collection_name} 不存在，创建新集合")
                self.retriever.create_collection()
                self.retriever.create_index()
            
            # 确保集合已加载
            self.retriever.client.load_collection(self.retriever.collection_name)
            
            for i, embedding in enumerate(all_embeddings):
                try:
                    page_num = i + 1
                    vector = embedding.float().numpy()
                    page_doc_id = doc_id_int * 1000 + page_num
                    
                    # 记录每个文档页面的详细信息
                    logger.debug(f"插入页面 {page_num}/{len(all_embeddings)}, 文档ID: {page_doc_id}")
                    
                    # 获取文件名和相对路径
                    file_name = os.path.basename(file_path)
                    image_name = os.path.basename(page_paths[i])
                    config_folder = None

                    # 尝试获取当前配置的文件夹路径
                    config_folder = None
                    if config and 'folder' in config:
                        config_folder = config['folder']
                    # 或者使用类变量
                    elif hasattr(self, 'config_dir') and self.config_dir:
                        for config_id, config in self.config_dir.items():
                            if 'folder' in config and file_path.startswith(config['folder']):
                                config_folder = config['folder']
                                break
                    elif hasattr(self, 'rag_configurations'):
                        for config_id, config in self.rag_configurations.items():
                            if 'folder' in config and file_path.startswith(config['folder']):
                                config_folder = config['folder']
                                break

                    # 确保路径是绝对路径
                    absolute_image_page_path = os.path.abspath(page_paths[i])
                    absolute_pdf_file_path = os.path.abspath(file_path)

                    data = {
                        "colbert_vecs": vector,
                        "doc_id": page_doc_id,
                        "filepath": absolute_pdf_file_path,  # 存储PDF文件绝对路径
                        "text_content": "",  # 关键：初始为空，不预先提取文本
                        "page_num": page_num,
                        "image_path": absolute_image_page_path,  # 存储图像绝对路径
                    }
                    
                    self.retriever.insert(data)
                    success_count += 1
                    
                    # 更新进度 - 使用回调函数
                    if progress_callback and task_id and (i % 10 == 0 or i == len(all_embeddings) - 1):
                        insert_progress = i / len(all_embeddings)
                        progress_callback(task_id, {
                            'current_step': 'inserting_data',
                            'progress': 80 + (insert_progress * 15),  # 80-95%
                            'current_page': i + 1,
                            'processed_pages': success_count,
                            'total_pages': len(all_embeddings)
                        })
                    
                    if (i + 1) % 100 == 0 or i == 0 or i == len(all_embeddings) - 1:
                        logger.info(f"已插入 {i + 1}/{len(all_embeddings)} 页")
                
                except Exception as e:
                    logger.error(f"处理页面 {i + 1} 时出错: {str(e)}")
            
            insert_data_time = time.time() - insert_data_start
            logger.info(f"插入数据耗时 {insert_data_time:.2f}秒")
            total_time = time.time() - convert_pdf_start
            logger.info(f"处理总耗时 {total_time:.2f}秒")
            
            # 保存文档信息到内存
            if not hasattr(self, 'documents'):
                self.documents = {}
            class DocumentInfo:
                def __init__(self, file_path, page_count, processed_count):
                    self.file_path = file_path
                    self.page_count = page_count
                    self.processed_count = processed_count
                    self.last_modified = os.path.getmtime(file_path)
                    # 只要有成功处理的页面，就标记为处理成功
                    self.processed = processed_count > 0
                    # 明确添加状态字段，供前端使用
                    self.status = "processed" if processed_count > 0 else "unprocessed"
            self.documents[doc_id_int] = DocumentInfo(file_path, len(images), success_count)
            logger.info(f"成功处理 {success_count}/{len(images)} 页")
            
            # 完成处理 - 更新进度
            if progress_callback and task_id:
                progress_callback(task_id, {
                    'current_step': 'finalizing',
                    'progress': 100,
                    'processed_pages': success_count,
                    'total_pages': len(images),
                    'status': 'completed'
                })
            
            # 返回处理结果
            return {
                "doc_id": doc_id_int,
                "file_path": file_path,
                "total_pages": len(images),
                "processed_pages": success_count,
                "success": success_count > 0,
                "status": "processed" if success_count > 0 else "unprocessed",
                "collection_name": self.retriever.collection_name,  # 添加集合名称
                "database_name": f"rag_{self.retriever.collection_name}"  # 添加数据库名称
            }
                
        except Exception as e:
            logger.error(f"处理PDF文件失败: {str(e)}")
            traceback.print_exc()
            
            # 处理失败 - 更新进度
            if progress_callback and task_id:
                progress_callback(task_id, {
                    'status': 'failed',
                    'error': str(e)
                })
            
            return False
            
    def search(self, query, top_k=5, search_id=None):
        """使用ColPali模型搜索相关文档，并在需要时实时进行PDF文本提取"""
        if not self.model or not self.processor or not self.retriever:
            logger.error("模型或检索器未初始化")
            return []
            
        try:
            # 记录查询开始时间
            start_time = time.time()
            
            # 创建搜索ID用于日志记录
            if not search_id:
                search_id = f"{int(time.time())}_{hashlib.md5(query.encode()).hexdigest()[:8]}"
            
            # 创建日志目录
            from pathlib import Path
            LOG_DIR = Path(__file__).parents[1] / "search_logs"
            LOG_DIR.mkdir(exist_ok=True)
            
            search_log_path = os.path.join(LOG_DIR, f"search_{search_id}.log")
            with open(search_log_path, "w", encoding="utf-8") as log_file:
                log_file.write(f"搜索查询: '{query}'\n")
                log_file.write(f"搜索ID: {search_id}\n")
                log_file.write(f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                log_file.write("=" * 50 + "\n\n")
            
            # 处理查询文本
            inputs = self.processor.process_queries([query])
            inputs = {k: v.to(self.model.device) for k, v in inputs.items()}

            # 使用半精度计算加速生成查询向量
            with torch.cuda.amp.autocast() if torch.cuda.is_available() else nullcontext():
                with torch.no_grad():
                    query_embedding = self.model(**inputs)
                
            # 将嵌入向量转移到CPU并转换为numpy数组
            query_vector = query_embedding[0].float().cpu().numpy()
            
            # 搜索相关文档，传入PDF处理函数用于延迟处理
            # 确认当前使用的集合名称
            collection_name = self.retriever.collection_name
            logger.info(f"使用集合 '{collection_name}' 进行搜索，查询: '{query[:50]}...'")
            
            # 确保Milvus集合已加载
            if hasattr(self.retriever.client, 'has_collection') and self.retriever.client.has_collection(collection_name):
                logger.info(f"确认集合 '{collection_name}' 已存在")
                # 确保集合已加载
                self.retriever.client.load_collection(collection_name)
                logger.info(f"已加载集合 '{collection_name}' 用于搜索")
            
            # 搜索相关文档，传入PDF文本提取函数用于延迟处理
            results = self.retriever.search(
                query_vector, 
                top_k, 
                text_extractor=self.extract_text_from_pdf_by_image_path,  # 使用PDF文本提取方法
                log_search_id=search_id  # 传入搜索ID用于文本提取日志
            )
            
            # 记录搜索结果数量
            logger.info(f"搜索返回了 {len(results)} 个结果")
            
            ## 格式化搜索结果
            formatted_results = []
            for score, doc_id, doc_info in results:
                # 从页面级ID推导出基础文档ID和页码
                base_doc_id = doc_id // 1000
                page_num = doc_id % 1000
                
                formatted_results.append({
                    "score": float(score),
                    "doc_id": doc_id,  # 保留完整的页面ID
                    "base_doc_id": base_doc_id,  # 添加基础文档ID便于跟踪
                    "file_path": doc_info["doc_path"],
                    "text_content": doc_info.get("text_content", ""),
                    "page_num": doc_info["page_num"],
                    "image_path": doc_info.get("image_path", ""),
                })
                
                logger.info(f"检索到结果 - 得分: {float(score):.4f}, 页面ID: {doc_id}, 页码: {page_num}")
            
            # 记录查询时间和结果
            elapsed_time = time.time() - start_time
            logger.info(f"查询处理完成：'{query[:50]}...'，找到 {len(formatted_results)} 个结果，耗时 {elapsed_time:.2f} 秒")
            
            # 记录查询日志
            self._log_search_results(query, formatted_results, search_id)
                
            return formatted_results
        except Exception as e:
            logger.error(f"搜索失败: {str(e)}")
            traceback.print_exc()
            return []
    
    def _log_search_results(self, query, results, search_id=None):
        """记录查询结果到日志文件"""
        try:
            # 如果没有提供搜索ID，生成一个
            if not search_id:
                search_id = f"{int(time.time())}_{hashlib.md5(query.encode()).hexdigest()[:8]}"
                
            # 使用提供的搜索ID创建日志文件名
            from pathlib import Path
            LOG_DIR = Path(__file__).parents[1] / "search_logs"
            LOG_DIR.mkdir(exist_ok=True)
            
            log_file = os.path.join(LOG_DIR, f"search_{search_id}.log")
            
            # 检查文件是否已存在，存在则附加，不存在则创建
            mode = "a" if os.path.exists(log_file) else "w"
            
            with open(log_file, mode, encoding="utf-8") as f:
                # 如果是新文件，添加查询信息
                if mode == "w":
                    f.write(f"查询时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                    f.write(f"查询内容: {query}\n\n")
                
                # 添加结果部分
                f.write(f"找到 {len(results)} 个结果:\n")
                
                for i, result in enumerate(results):
                    f.write(f"\n结果 {i+1}:\n")
                    f.write(f"文档ID: {result['doc_id']}\n")
                    f.write(f"文件路径: {result['file_path']}\n")
                    f.write(f"页码: {result['page_num']}\n")
                    f.write(f"图像路径: {result.get('image_path', '未知')}\n")
                    f.write(f"相似度得分: {result['score']}\n")
                    
                    # 提取并记录文本内容
                    text_content = result.get('text_content', '')
                    f.write(f"文本内容 ({len(text_content)} 字符):\n")
                    if len(text_content) > 1000:  # 限制过长的内容
                        f.write(f"{text_content[:1000]}\n...(内容已截断)\n")
                    else:
                        f.write(f"{text_content}\n")
                    f.write("-" * 50 + "\n")
            
            logger.info(f"查询日志已保存至: {log_file}")
            return log_file
        except Exception as e:
            logger.error(f"记录查询日志失败: {str(e)}")
            return None

    def close(self):
        """释放资源"""
        try:
            # 释放GPU内存
            if hasattr(self, 'model') and self.model is not None:
                del self.model
                
            if hasattr(self, 'processor') and self.processor is not None:
                del self.processor
                
            # 清理GPU缓存
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                
            logger.info("ColPali资源已释放")
            return True
        except Exception as e:
            logger.error(f"释放资源时出错: {str(e)}")
            return False
