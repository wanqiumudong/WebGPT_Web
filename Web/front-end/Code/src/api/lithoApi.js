import request from '../utils/request';
import { fetchType } from './constant';
import axios from "axios";
import { LITHO_BASE_URL } from '../config/endpoints';
import { createStreamRequest } from '../utils/apiUtils';

const LITHO_API_BASE_URL = LITHO_BASE_URL;

export const fetchLitho = async (data) => {
  try {
    const response = await request({
      baseUrl: LITHO_API_BASE_URL,
      url: '/generate',
      data,
      method: fetchType.post
    });
    return response.content || response.response;
  } catch (e) {
    console.error('Litho请求失败:', e);
    return '后端算法还在优化中哦';
  }
};

export const fetchLithoStreaming = (data, onChunkReceived, onComplete, onError) => {
  const streamRequest = createStreamRequest({
    baseUrl: LITHO_API_BASE_URL,
    data,
    onChunk: (streamData) => {
      onChunkReceived && onChunkReceived(streamData);
    },
    onComplete,
    onError: (error) => {
      console.error('Litho流式API请求出错:', error);
      onError && onError(error);
    }
  });

  return {
    cancel: async () => {
      try {
        if (onChunkReceived) {
          onChunkReceived({
            aborted: true,
            chunk: "",
            is_complete: true
          });
        }

        await streamRequest.cancel();
        onComplete && onComplete();
        return true;
      } catch (e) {
        console.error('取消Litho流式请求时出错:', e);
        onComplete && onComplete();
        throw e;
      }
    }
  };
};

export const uploadLithoFile = (formData, config) => {
  return axios
    .post(`${LITHO_API_BASE_URL}/uploadFile`, formData, { ...config })
    .catch((e) => {
      console.error('❌ Litho文件上传失败:', e);
      return e;
    });
};
