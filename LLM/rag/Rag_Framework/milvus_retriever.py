"""
Milvus检索器模块 - 使用Milvus进行ColBERT检索
- 与Milvus数据库交互，存储和检索文档向量。
milvus_retriever.py
"""

import os
import time
import logging
import hashlib
import traceback
import numpy as np
import concurrent.futures
from pymilvus import MilvusClient, DataType

logger = logging.getLogger("ColPali-RAG-Manager")

class MilvusColbertRetriever:
    """使用Milvus进行ColBERT检索的类，优化了存储和搜索效率，支持延迟PDF处理"""
    
    def __init__(self, milvus_client, collection_name, dim=128):
        """初始化检索器"""
        self.collection_name = collection_name
        self.client = milvus_client
        
        # 始终尝试加载集合,忽略不存在的错误
        try:
            if self.client.has_collection(collection_name=self.collection_name):
                self.client.load_collection(collection_name)
                logger.info(f"已加载集合 {collection_name}")
        except Exception as e:
            logger.warning(f"加载集合时出错 (可能是新集合): {str(e)}")
                
        self.dim = dim
        self.pdf_text_extracted_count = 0

    def create_collection(self):
        """创建新的Milvus集合，确保doc_id字段与参考代码一致"""
        try:
            # 检查并删除已存在的集合
            if self.client.has_collection(collection_name=self.collection_name):
                logger.info(f"集合 {self.collection_name} 已存在，将删除并重建")
                self.client.drop_collection(collection_name=self.collection_name)
            
            # 创建schema
            schema = self.client.create_schema(
                auto_id=True,
                enable_dynamic_fields=True,
            )
            
            # 添加标准字段
            schema.add_field(field_name="pk", datatype=DataType.INT64, is_primary=True)
            schema.add_field(field_name="vector", datatype=DataType.FLOAT_VECTOR, dim=self.dim)
            schema.add_field(field_name="seq_id", datatype=DataType.INT16)
            schema.add_field(field_name="doc_id", datatype=DataType.INT64)  # 确保使用INT64
            
            # 添加文档信息字段，增加长度限制以处理较大文本
            schema.add_field(field_name="doc", datatype=DataType.VARCHAR, max_length=65535)
            schema.add_field(field_name="text_content", datatype=DataType.VARCHAR, max_length=65535)
            schema.add_field(field_name="page_num", datatype=DataType.INT16)
            schema.add_field(field_name="image_path", datatype=DataType.VARCHAR, max_length=512)
            
            # 创建集合
            self.client.create_collection(
                collection_name=self.collection_name, schema=schema
            )
            
            logger.info(f"成功创建集合 {self.collection_name}")
            
            # 创建索引
            index_params = self.client.prepare_index_params()
            index_params.add_index(
                field_name="vector",
                index_name="vector_index",
                index_type="HNSW",     # 使用HNSW
                metric_type="IP",      # 内积相似度
                params={
                    "M": 16,          
                    "efConstruction": 200  
                },
            )
            
            # 创建索引
            self.client.create_index(
                collection_name=self.collection_name, index_params=index_params
            )
            logger.info(f"集合 {self.collection_name} 创建HNSW向量索引成功")
            
            # 加载集合 - 只加载一次
            try:
                self.client.load_collection(collection_name=self.collection_name)
                logger.info(f"集合 {self.collection_name} 已加载")
            except Exception as load_err:
                logger.warning(f"加载集合失败: {str(load_err)}")
            
            # 对于新集合，创建标量索引
            try:
                # 创建标量索引，提高查询效率
                scalar_index_params = self.client.prepare_index_params()
                scalar_index_params.add_index(
                    field_name="doc_id",
                    index_name="int64_index",
                    index_type="INVERTED",
                )
                self.client.create_index(
                    collection_name=self.collection_name, index_params=scalar_index_params
                )
                logger.info(f"已为集合 {self.collection_name} 创建标量索引")
            except Exception as scalar_err:
                logger.warning(f"为集合 {self.collection_name} 创建标量索引失败: {str(scalar_err)}")
            
            return True
        except Exception as e:
            logger.error(f"创建集合失败: {str(e)}")
            traceback.print_exc()
            return False
        
    def create_index(self):
        """创建向量索引以实现快速相似度搜索"""
        try:
            # 检查索引是否已存在
            try:
                index_info = self.client.describe_index(
                    collection_name=self.collection_name, 
                    index_name="vector_index"
                )
                logger.info(f"索引已存在，无需重复创建: {index_info}")
                return True
            except Exception:
                # 索引不存在，继续创建
                pass
                
            # 创建新索引，使用HNSW
            index_params = self.client.prepare_index_params()
            index_params.add_index(
                field_name="vector",
                index_name="vector_index",
                index_type="HNSW",     # 使用HNSW，这是Standalone性能最好的索引类型
                metric_type="IP",      # 内积相似度
                params={
                    "M": 16,           # 构建图索引时的邻居数量
                    "efConstruction": 200  # 构建索引时搜索深度
                },
            )
            # 创建索引
            self.client.create_index(
                collection_name=self.collection_name, index_params=index_params
            )
            
            # 创建后确保集合重新加载
            self.client.load_collection(collection_name=self.collection_name)
            
            logger.info(f"集合 {self.collection_name} 创建HNSW向量索引成功")
            return True
        except Exception as e:
            logger.error(f"创建索引失败: {str(e)}")
            traceback.print_exc()
            return False

    def create_scalar_index(self):
        """为doc_id字段创建标量索引，提高按文档ID查询的性能 - 已优化减少冗余操作"""
        try:
            # 首先检查索引是否已存在
            try:
                index_info = self.client.describe_index(
                    collection_name=self.collection_name, 
                    index_name="int64_index"
                )
                logger.info(f"标量索引已存在，无需重复创建: {index_info}")
                return True
            except Exception:
                # 索引不存在，继续创建
                pass
                
            # 创建标量索引
            index_params = self.client.prepare_index_params()
            index_params.add_index(
                field_name="doc_id",
                index_name="int64_index",  # 改为更准确的名称
                index_type="INVERTED",
            )
            self.client.create_index(
                collection_name=self.collection_name, index_params=index_params
            )
            logger.info(f"已为集合 {self.collection_name} 创建标量索引")
            return True
        except Exception as e:
            logger.warning(f"创建标量索引失败: {str(e)}")
            return False

    def search(self, data, topk, text_extractor=None, log_search_id=None):
        """执行向量搜索，返回最相关的结果，并执行延迟PDF文本提取"""
        # 记录搜索日志
        logger.info(f"开始执行向量搜索，topk={topk}，搜索ID={log_search_id}")
        search_start_time = time.time()
        
        # 确保集合已加载
        try:
            if self.client.has_collection(collection_name=self.collection_name):
                self.client.load_collection(self.collection_name)
                logger.info(f"已确保集合 {self.collection_name} 已加载")
        except Exception as load_err:
            logger.warning(f"加载集合失败: {str(load_err)}")
        
        # 设置一个合理的初始限制
        initial_limit = min(1000, topk * 10)  # 允许获取更多结果
        logger.info(f"执行Standalone优化搜索，初始限制={initial_limit}")    

        # 针对Standalone优化的搜索参数
        search_params = {
            "metric_type": "IP", 
            "params": {
                "ef": max(initial_limit, 64)  # 确保ef至少等于 initial_limit，并且不低于基线值
            }
        }
        
        try:
            # 仅获取必要字段，减少数据传输
            results = self.client.search(
                self.collection_name,
                data,
                limit=initial_limit,
                output_fields=["seq_id", "doc_id", "doc"],  # 只获取基本字段
                search_params=search_params,
            )
            
            # 记录搜索耗时
            search_time = time.time() - search_start_time
            logger.info(f"初始搜索完成，耗时: {search_time:.2f}秒")
        except Exception as search_err:
            logger.error(f"初始搜索失败: {str(search_err)}")
            return []  # 搜索失败，返回空结果
        
        # 提取唯一文档ID - 优化为按初始得分排序
        doc_id_scores = []
        for r_id in range(len(results)):
            for r in range(len(results[r_id])):
                entity = results[r_id][r]["entity"]
                doc_id = entity["doc_id"]
                score = results[r_id][r]["distance"]  # 初始得分
                doc_id_scores.append((doc_id, score))

        # 去重并按得分排序
        unique_doc_ids = {}
        for doc_id, score in doc_id_scores:
            if doc_id not in unique_doc_ids or score > unique_doc_ids[doc_id]:
                unique_doc_ids[doc_id] = score

        # 按得分排序，优先处理可能相关性更高的文档
        sorted_doc_ids = sorted(unique_doc_ids.items(), key=lambda x: x[1], reverse=True)
        
        # 只保留文档ID
        doc_ids = [doc_id for doc_id, _ in sorted_doc_ids]

        # 记录搜索范围
        logger.info(f"将处理 {len(doc_ids)} 个唯一文档ID")

        scores = []
        rerank_start_time = time.time()

        # 定义一个更优化的重排序函数
        def rerank_single_doc(doc_id, data, client, collection_name):
            """优化的文档重排序函数，专为Standalone设计"""
            try:
                # 分两步查询，先获取元数据，再获取向量
                # 步骤1: 获取文档基本信息
                doc_info_query = client.query(
                    collection_name=collection_name,
                    filter=f"doc_id == {doc_id} AND seq_id == 0",  # 只获取第一条记录的元数据
                    output_fields=["doc", "text_content", "page_num", "image_path"],
                    limit=1
                )
                
                if not doc_info_query:
                    return None
                    
                # 提取元数据
                doc_info = doc_info_query[0]["doc"] if doc_info_query else ""
                text_content = doc_info_query[0].get("text_content", "") if doc_info_query else ""
                page_num = doc_info_query[0].get("page_num", 0) if doc_info_query else 0
                image_path = doc_info_query[0].get("image_path", "") if doc_info_query else ""
                
                # 步骤2: 获取向量数据 - 有时向量数量太多，分批获取
                doc_vecs = []
                offset = 0
                batch_size = 500  # 一次获取500个向量
                
                while True:
                    try:
                        batch_vecs = client.query(
                            collection_name=collection_name,
                            filter=f"doc_id == {doc_id}", 
                            output_fields=["vector"],
                            offset=offset,
                            limit=batch_size
                        )
                        
                        if not batch_vecs:
                            break
                            
                        doc_vecs.extend([vec["vector"] for vec in batch_vecs])
                        
                        if len(batch_vecs) < batch_size:
                            break
                            
                        offset += batch_size
                        
                        # 限制向量数量，避免处理太多
                        if len(doc_vecs) >= 2000:  # 最多处理2000个向量
                            break
                    except Exception as batch_err:
                        logger.debug(f"获取向量批次出错: {str(batch_err)}")
                        break
                
                if not doc_vecs:
                    return None
                    
                # 计算MaxSim分数
                doc_vecs_array = np.vstack(doc_vecs)
                score = np.dot(data, doc_vecs_array.T).max(1).sum()
                
                return (score, doc_id, {"doc_path": doc_info, "text_content": text_content, "page_num": page_num, "image_path": image_path})
            except Exception as e:
                logger.error(f"处理文档 {doc_id} 时出错: {str(e)}")
                return None

        # 使用分批并行重排序
        batch_size = 20  # 每批处理10个文档
        max_time = 60    # 设置最大处理时间为30秒

        # 定义一个函数来处理批次
        def process_batch(batch_docs):
            batch_scores = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(20, len(batch_docs))) as executor:
                futures = {
                    executor.submit(
                        rerank_single_doc, doc_id, data, self.client, self.collection_name
                    ): doc_id
                    for doc_id in batch_docs
                }
                
                for future in concurrent.futures.as_completed(futures):
                    try:
                        result = future.result(timeout=5)  # 每个任务最多5秒
                        if result:
                            batch_scores.append(result)
                    except concurrent.futures.TimeoutError:
                        logger.warning(f"处理文档超时，跳过")
                    except Exception as e:
                        logger.error(f"处理文档时出错: {str(e)}")
            
            return batch_scores

        # 分批处理所有文档
        for i in range(0, len(doc_ids), batch_size):
            # 检查是否已超过最大处理时间
            if time.time() - rerank_start_time > max_time:
                logger.warning(f"重排序已超过最大时间限制({max_time}秒)，提前结束处理")
                break
                
            batch = doc_ids[i:i+batch_size]
            batch_scores = process_batch(batch)
            scores.extend(batch_scores)
            
            # 记录进度
            logger.info(f"已完成重排序: {min(i+batch_size, len(doc_ids))}/{len(doc_ids)} 个文档")

        logger.info(f"重排序完成，耗时: {time.time() - rerank_start_time:.2f}秒，获得 {len(scores)} 个有效结果")
        
        # 按得分排序并选择topk个结果
        scores.sort(key=lambda x: x[0], reverse=True)
        final_scores = scores[:topk] if len(scores) >= topk else scores
        logger.info(f"筛选出 {len(final_scores)}/{len(scores)} 个最终结果进行PDF文本处理")
        
        # 只对最终结果进行PDF文本提取
        if text_extractor:
            logger.info(f"Search ID {log_search_id}: 开始对 {len(final_scores)} 个最终结果进行PDF文本提取 (MilvusRetriever)。")
            for i, (score, doc_id, doc_info) in enumerate(final_scores):
                image_path = doc_info.get("image_path", "")
                page_num = doc_info.get("page_num", 0)
                
                # 如果text_content已存在,则跳过
                if doc_info.get("text_content"):
                    logger.info(f"Search ID {log_search_id}: 文档 {doc_id}, 页面 {page_num} 已有文本内容,跳过PDF文本提取 (MilvusRetriever)。")
                    continue
                    
                if image_path:
                    if os.path.exists(image_path):
                        logger.info(f"Search ID {log_search_id}: 对文档 {doc_id}, 页面 {page_num} 执行PDF文本提取: {image_path} (MilvusRetriever)")
                        try:
                            # text_extractor 现在是 ColPaliManager 的 extract_text_from_pdf_by_image_path
                            extraction_result = text_extractor(image_path)
                            
                            if extraction_result and extraction_result.get("success"):
                                extracted_text = extraction_result.get("text", "")
                                if extracted_text:
                                    logger.info(f"Search ID {log_search_id}: PDF文本提取成功,长度: {len(extracted_text)} 字符,来自 {image_path} (MilvusRetriever)")
                                    self.pdf_text_extracted_count += 1
                                    final_scores[i] = (score, doc_id, {**doc_info, "text_content": extracted_text})
                                else:
                                    logger.warning(f"Search ID {log_search_id}: PDF文本提取结果为空: {image_path} (MilvusRetriever)")
                                    final_scores[i] = (score, doc_id, {**doc_info, "text_content": ""})
                            else:
                                error_msg = extraction_result.get("error", "未知错误") if extraction_result else "提取失败"
                                logger.warning(f"Search ID {log_search_id}: PDF文本提取失败: {image_path}, 错误: {error_msg} (MilvusRetriever)")
                                final_scores[i] = (score, doc_id, {**doc_info, "text_content": ""})
                                
                        except Exception as e:
                            logger.error(f"Search ID {log_search_id}: PDF文本提取过程出错 for {image_path}: {str(e)} (MilvusRetriever)")
                            import traceback
                            traceback.print_exc()
                            final_scores[i] = (score, doc_id, {**doc_info, "text_content": ""})
                    else:
                        logger.warning(f"Search ID {log_search_id}: 图像文件不存在: {image_path},无法执行PDF文本提取 (MilvusRetriever)。")
                        final_scores[i] = (score, doc_id, {**doc_info, "text_content": ""})
                else:
                    logger.warning(f"Search ID {log_search_id}: 文档 {doc_id}, 页面 {page_num} 的image_path为空,无法执行PDF文本提取 (MilvusRetriever)。")
                    final_scores[i] = (score, doc_id, {**doc_info, "text_content": ""})

        # 记录最终返回的结果
        logger.info(f"最终返回 {len(final_scores)} 个结果，要求的top_k: {topk}")
        if final_scores:
            logger.info(f"最高得分结果: 文档ID={final_scores[0][1]}, 得分={final_scores[0][0]:.4f}")

        # 返回最终结果
        return final_scores

    def insert(self, data):
        """插入向量数据到Milvus集合，确保数据类型一致"""
        colbert_vecs = [vec for vec in data["colbert_vecs"]]
        seq_length = len(colbert_vecs)
        
        # 确保doc_id是整数类型 - 与参考代码一致
        doc_id = data["doc_id"]
        if isinstance(doc_id, str):
            try:
                # 1. 检查是否是十六进制格式
                if doc_id.startswith('0x'):
                    doc_id = int(doc_id, 16) & 0xFFFFFFFF  # 限制为32位整数
                # 2. 尝试转换为整数
                else:
                    doc_id = int(doc_id)
            except ValueError:
                # 3. 如果无法转换，使用一致的哈希算法
                # 只取前8位十六进制数，确保生成稳定ID
                doc_id = int(hashlib.md5(doc_id.encode()).hexdigest()[:8], 16)
                logger.debug(f"为字符串生成文档ID: {doc_id} (字符串哈希)")
        
        doc_ids = [doc_id for i in range(seq_length)]
        seq_ids = list(range(seq_length))
        docs = [""] * seq_length
        docs[0] = data["filepath"]
        
        # 可选文本内容和页码
        text_contents = [""] * seq_length
        if "text_content" in data:
            text_contents[0] = data["text_content"]
            
        page_nums = [data.get("page_num", 0) for i in range(seq_length)]
        image_paths = [""] * seq_length
        if "image_path" in data:
            image_paths[0] = data["image_path"]
        
        # 批量插入数据
        self.client.insert(
            self.collection_name,
            [
                {
                    "vector": colbert_vecs[i],
                    "seq_id": seq_ids[i],
                    "doc_id": doc_ids[i],
                    "doc": docs[i],
                    "text_content": text_contents[i],
                    "page_num": page_nums[i],
                    "image_path": image_paths[i]
                }
                for i in range(seq_length)
            ],
        )

    def delete_document(self, doc_id):
        """优化的文档删除方法"""
        try:
            # 确保doc_id是整数
            if isinstance(doc_id, str):
                try:
                    if doc_id.startswith('0x'):
                        doc_id_int = int(doc_id, 16)
                    else:
                        hash_obj = hashlib.md5(doc_id.encode())
                        doc_id_int = int(hash_obj.hexdigest()[:8], 16)
                except ValueError:
                    hash_obj = hashlib.md5(doc_id.encode())
                    doc_id_int = int(hash_obj.hexdigest()[:8], 16)
            else:
                doc_id_int = doc_id
                
            logger.info(f"开始删除文档: {doc_id_int}")
            
            # 构建删除表达式
            min_doc_id = doc_id_int
            max_doc_id = doc_id_int + 1
            delete_expr = f"doc_id >= {min_doc_id} AND doc_id < {max_doc_id}"
            
            # 执行删除操作
            delete_start = time.time()
            result = self.client.delete(
                collection_name=self.collection_name,
                filter=delete_expr
            )
            delete_time = time.time() - delete_start
            
            # 同步刷新（保持简单）
            flush_start = time.time()
            self.client.flush(self.collection_name)
            flush_time = time.time() - flush_start
            
            logger.info(f"文档删除完成，删除耗时: {delete_time:.2f}秒，刷新耗时: {flush_time:.2f}秒")
            
            return {
                "success": True,
                "deleted_records": -1,  # 不查询具体数量，节省时间
                "doc_id": doc_id_int
            }
            
        except Exception as e:
            logger.error(f"删除文档 {doc_id} 失败: {str(e)}")
            traceback.print_exc()
            return {
                "success": False,
                "error": str(e),
                "doc_id": doc_id
            }